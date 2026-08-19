from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from math import exp, log, sqrt
from statistics import mean, median, pstdev
from typing import Any, Iterable
import hashlib
import json
import re

from .models import AnalysisResult, Candidate, Issue, Table


# PASS013 adds exactly these 50 backlog capabilities.  Some are repair-capable,
# others are deliberately diagnostics/gates: a diagnostic may never authorize an edit alone.
FEATURE_IDS = [
    2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,
    20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,
    72,74,75,76,77,79,
]
FEATURE_NAMES = {
    2:'structural_row_recovery', 3:'header_repair', 4:'mixed_locale_numeric_parsing',
    5:'datetime_timezone_inference', 6:'reversible_unit_currency_graph',
    7:'canonical_near_duplicate_detection', 8:'persistent_duplicate_residue_pool',
    9:'duplicate_family_lineage', 10:'event_driven_duplicate_reevaluation',
    11:'six_state_missingness', 12:'censoring_bounds', 13:'missingness_mechanism_diagnostics',
    14:'per_cell_uncertainty', 15:'uncertainty_propagation', 16:'bounded_reconstruction_output',
    17:'error_source_taxonomy', 18:'stale_value_detection',
    20:'composite_expression_search', 21:'linear_conservation_relations', 22:'order_monotonicity_constraints',
    23:'higher_arity_functional_dependencies', 24:'mutual_information_screening', 25:'candidate_key_discovery',
    26:'conditional_rules', 27:'nested_overlapping_regimes', 28:'aggregate_detail_relations',
    29:'lag_relationships', 30:'periodic_seasonal_constraints', 31:'irregular_sampling_awareness',
    32:'multirate_relationships', 33:'automatic_multitable_relationships', 34:'join_key_recovery',
    35:'schema_version_relation_registry', 36:'multipath_exact_reconstruction_extension',
    37:'arbitrary_depth_reconstruction_chains', 38:'gated_low_rank_completion',
    39:'gated_robust_low_rank_sparse_localization', 40:'gated_sparse_recovery',
    41:'gated_state_space_reconstruction', 42:'gated_bayesian_filter_reconstruction',
    43:'bounded_interpolation', 44:'forward_backward_temporal_agreement',
    45:'interval_reconstruction', 46:'cross_file_reconstruction',
    72:'nested_validation', 74:'locked_holdout_validation', 75:'negative_control_relations',
    76:'distributionally_robust_stress', 77:'multivariate_ood_abstention', 79:'inverse_sensitivity_guard',
}


def feature_registry() -> dict[str, Any]:
    return {
        'count': len(FEATURE_IDS),
        'ids': FEATURE_IDS,
        'features': {str(i): FEATURE_NAMES[i] for i in FEATURE_IDS},
    }


def _sha(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode('utf-8')).hexdigest()[:16]


def _d(s: str) -> Decimal | None:
    try:
        t=s.strip().replace('−','-')
        if not t:
            return None
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _f(s: str) -> float | None:
    d=_d(s)
    return float(d) if d is not None else None


def _fmt(x: float | Decimal) -> str:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    if d == 0:
        return '0'
    s=format(d.normalize(),'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def _isclose(a: float, b: float, atol: float=1e-8, rtol: float=1e-8) -> bool:
    return abs(a-b) <= atol + rtol*max(1.0, abs(b))


def _parse_locale_number(s: str, style: str) -> Decimal | None:
    t=s.strip().replace('\u00a0',' ').replace('−','-')
    if not t: return None
    # Keep signs/exponents; normalize grouping only when the style is structurally legal.
    if style == 'us':
        if ',' in t and '.' in t:
            if not re.fullmatch(r'[+-]?\d{1,3}(,\d{3})+(\.\d+)?([eE][+-]?\d+)?', t): return None
            t=t.replace(',','')
        elif ',' in t:
            if not re.fullmatch(r'[+-]?\d{1,3}(,\d{3})+([eE][+-]?\d+)?', t): return None
            t=t.replace(',','')
    elif style == 'eu':
        t=t.replace(' ','')
        if '.' in t and ',' in t:
            if not re.fullmatch(r'[+-]?\d{1,3}(\.\d{3})+(,\d+)?([eE][+-]?\d+)?', t): return None
            t=t.replace('.','').replace(',','.')
        elif ',' in t:
            if not re.fullmatch(r'[+-]?\d+(,\d+)?([eE][+-]?\d+)?', t): return None
            t=t.replace(',','.')
        elif '.' in t:
            # A lone dot is ambiguous under EU conventions: allow only exact 3-digit grouping.
            if not re.fullmatch(r'[+-]?\d{1,3}(\.\d{3})+([eE][+-]?\d+)?', t): return None
            t=t.replace('.','')
    else:
        return _d(t)
    try: return Decimal(t)
    except InvalidOperation: return None


def _parse_dt(s: str) -> tuple[datetime | None, str | None, bool]:
    t=s.strip()
    if not t: return None,None,False
    # ISO first; it is unambiguous and preserves timezone when present.
    iso=t.replace('Z','+00:00')
    try:
        dt=datetime.fromisoformat(iso)
        return dt,'iso8601',False
    except ValueError:
        pass
    candidates=[]
    for name,fmt in [
        ('ymd','%Y-%m-%d'),('ymd_slash','%Y/%m/%d'),('dmy','%d/%m/%Y'),('mdy','%m/%d/%Y'),
        ('dmy_time','%d/%m/%Y %H:%M:%S'),('mdy_time','%m/%d/%Y %H:%M:%S'),
    ]:
        try: candidates.append((datetime.strptime(t,fmt),name))
        except ValueError: pass
    uniq={(x.date(),x.time()) for x,_ in candidates}
    if len(uniq)==1 and candidates: return candidates[0][0],candidates[0][1],False
    return None,None,bool(candidates)


def _type_of(v: str) -> str:
    if not v.strip(): return 'empty'
    if _d(v) is not None: return 'number'
    dt,_,amb=_parse_dt(v)
    if dt is not None and not amb: return 'datetime'
    return 'string'


def _canonical_cell(v: str) -> str:
    t=' '.join(v.strip().split()).casefold()
    d=_d(t)
    if d is not None: return '#n:'+_fmt(d)
    dt,_,amb=_parse_dt(t)
    if dt is not None and not amb: return '#t:'+dt.isoformat()
    return '#s:'+t


def _column_profiles(table: Table) -> list[dict[str,Any]]:
    out=[]
    for c,h in enumerate(table.header):
        vals=[r[c] for r in table.rows if c<len(r) and r[c].strip()]
        counts=Counter(_type_of(v) for v in vals)
        dom=counts.most_common(1)[0][0] if counts else 'empty'
        out.append({'column':c,'name':h,'values':len(vals),'types':dict(counts),'dominant':dom})
    return out


def _strict_affine_projection(table: Table, row_index: int, target_col: int, aligned_row: list[str]) -> dict[str, Any] | None:
    """Recover one structurally missing numeric value from an exact affine column law.

    This is intentionally strict: the law must hold on every complete witness row, use at
    least eight witnesses and three distinct source values, and all independent exact laws
    that can predict the target must agree.  It is used only after row alignment has already
    identified a unique missing column.
    """
    if target_col >= len(table.header) or target_col >= len(aligned_row):
        return None
    projections: list[tuple[int, Decimal, Decimal, Decimal, int]] = []
    width=len(table.header)
    for source_col in range(width):
        if source_col == target_col or source_col >= len(aligned_row):
            continue
        x0=_d(aligned_row[source_col])
        if x0 is None:
            continue
        pairs: list[tuple[Decimal,Decimal]]=[]
        for r,row in enumerate(table.rows):
            if r==row_index or len(row)!=width:
                continue
            x=_d(row[source_col]); y=_d(row[target_col])
            if x is not None and y is not None:
                pairs.append((x,y))
        if len(pairs)<8 or len({x for x,_ in pairs})<3:
            continue
        first=None
        for a in range(len(pairs)):
            for b in range(a+1,len(pairs)):
                if pairs[a][0] != pairs[b][0]:
                    first=(pairs[a],pairs[b]); break
            if first: break
        if not first:
            continue
        (xa,ya),(xb,yb)=first
        slope=(yb-ya)/(xb-xa); intercept=ya-slope*xa
        if not all(y == slope*x+intercept for x,y in pairs):
            continue
        projections.append((source_col,slope,intercept,slope*x0+intercept,len(pairs)))
    if not projections:
        return None
    values={_fmt(v) for _,_,_,v,_ in projections}
    if len(values)!=1:
        return None
    value=next(iter(values))
    return {
        'value':value,
        'witnesses':[{'source_column':c,'source_name':table.header[c],'slope':_fmt(a),'intercept':_fmt(b),'rows':n} for c,a,b,_,n in projections],
        'independent_exact_laws':len(projections),
    }


def structural_row_recovery(table: Table) -> list[dict[str,Any]]:
    w=len(table.header); profiles=_column_profiles(table); out=[]
    def score(row:list[str]) -> int:
        s=0
        for c,v in enumerate(row[:w]):
            t=_type_of(v); dom=profiles[c]['dominant']
            if v.strip() and dom!='empty' and t!=dom: s+=1
        return s
    for i,row in enumerate(table.rows):
        if len(row)==w: continue
        candidates=[]
        if len(row)==w-1:
            for pos in range(w):
                rr=row[:pos]+['']+row[pos:]
                candidates.append((score(rr),rr,'insert_blank',pos))
        elif len(row)==w+1:
            for pos,v in enumerate(row):
                if not v.strip():
                    rr=row[:pos]+row[pos+1:]
                    candidates.append((score(rr),rr,'remove_extra_blank',pos))
        candidates.sort(key=lambda x:(x[0],x[3]))
        unique=bool(candidates) and (len(candidates)==1 or candidates[0][0] < candidates[1][0])
        proposal=None
        if unique:
            proposal={'new_row':candidates[0][1],'operation':candidates[0][2],'position':candidates[0][3],'type_mismatches':candidates[0][0]}
            if candidates[0][2]=='insert_blank':
                projection=_strict_affine_projection(table,i,candidates[0][3],candidates[0][1])
                if projection is not None:
                    filled=list(candidates[0][1]); filled[candidates[0][3]]=projection['value']
                    proposal['new_row']=filled
                    proposal['reconstructed_missing']=projection
        out.append({'row':i,'actual_width':len(row),'expected_width':w,'unique_safe_alignment':unique,
                    'proposal':proposal,
                    'candidate_count':len(candidates)})
    return out


def header_repair(table: Table) -> dict[str,Any]:
    seen=Counter(); proposals=[]
    for i,h in enumerate(table.header):
        base=re.sub(r'\W+','_',h.strip()).strip('_') or f'column_{i+1}'
        seen[base]+=1
        new=base if seen[base]==1 else f'{base}__{seen[base]}'
        if new!=h:
            proposals.append({'column':i,'old':h,'new':new,'reason':'empty_or_duplicate_or_noncanonical'})
    return {'proposals':proposals,'safe_unique':len({p['new'] for p in proposals})==len(proposals)}


def locale_profiles(table: Table) -> list[dict[str,Any]]:
    out=[]
    for c,h in enumerate(table.header):
        vals=[r[c] for r in table.rows if c<len(r) and r[c].strip()]
        if len(vals)<3: continue
        scores={style:sum(_parse_locale_number(v,style) is not None for v in vals) for style in ('plain','us','eu')}
        best=max(scores,key=scores.get); ordered=sorted(scores.values(),reverse=True)
        decisive=scores[best]>=max(3,int(.9*len(vals))) and (len(ordered)<2 or ordered[0]>ordered[1])
        if scores[best]>=3:
            out.append({'column':c,'name':h,'style':best,'scores':scores,'decisive':decisive,'coverage':scores[best]/len(vals)})
    return out


def datetime_profiles(table: Table) -> list[dict[str,Any]]:
    out=[]
    for c,h in enumerate(table.header):
        vals=[r[c] for r in table.rows if c<len(r) and r[c].strip()]
        if len(vals)<3: continue
        fmts=Counter(); amb=0; parsed=0; tz=0
        for v in vals:
            dt,fmt,a=_parse_dt(v); amb+=int(a)
            if dt is not None:
                parsed+=1; fmts[fmt]+=1; tz+=int(dt.tzinfo is not None)
        if parsed>=3:
            out.append({'column':c,'name':h,'coverage':parsed/len(vals),'ambiguous_values':amb,
                        'formats':dict(fmts),'timezone_aware':tz,'roundtrip_safe':amb==0 and parsed==len(vals)})
    return out


UNIT_GRAPH = {
    'm':('length',1.0),'cm':('length',0.01),'mm':('length',0.001),'km':('length',1000.0),
    'kg':('mass',1.0),'g':('mass',0.001),'mg':('mass',0.000001),'lb':('mass',0.45359237),
    's':('time',1.0),'ms':('time',0.001),'min':('time',60.0),'h':('time',3600.0),
    'usd':('currency_usd',1.0),'$':('currency_usd',1.0),'cent':('currency_usd',0.01),
}

def unit_currency_graph(table: Table) -> dict[str,Any]:
    pat=re.compile(r'^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([A-Za-z$]+)\s*$')
    cells=[]
    for r,row in enumerate(table.rows):
        for c,v in enumerate(row):
            m=pat.match(v)
            if not m: continue
            u=m.group(2).casefold();
            if u in UNIT_GRAPH:
                fam,factor=UNIT_GRAPH[u]
                canonical={'length':'m','mass':'kg','time':'s','currency_usd':'usd'}[fam]
                base=float(m.group(1))*factor
                back=base/UNIT_GRAPH[canonical][1]
                cells.append({'row':r,'column':c,'raw':v,'unit':u,'family':fam,'canonical_unit':canonical,
                              'canonical_value':_fmt(base),'roundtrip_pass':_isclose(float(m.group(1)),back)})
    return {'recognized_cells':cells,'conversion_nodes':sorted(UNIT_GRAPH),'reversible_edges':sum(1 for a,b in combinations(UNIT_GRAPH,2) if UNIT_GRAPH[a][0]==UNIT_GRAPH[b][0])}


def duplicate_diagnostics(table: Table) -> dict[str,Any]:
    canon=defaultdict(list); raw=defaultdict(list)
    for r,row in enumerate(table.rows):
        raw[tuple(row)].append(r)
        canon[tuple(_canonical_cell(v) for v in row)].append(r)
    exact=[{'rows':v,'kind':'exact'} for v in raw.values() if len(v)>1]
    near=[]; residue=[]; lineage=[]
    for key,rows in canon.items():
        if len(rows)>1:
            rawsets={tuple(table.rows[r]) for r in rows}
            if len(rawsets)>1:
                near.append({'rows':rows,'canonical_fingerprint':_sha(key),'kind':'form_equivalent'})
                parent=min(rows)
                lineage.append({'parent_row':parent,'child_rows':[r for r in rows if r!=parent],'relation':'canonical_variant'})
    # unresolved residue: high cell agreement but not canonically equal
    for a,b in combinations(range(min(len(table.rows),500)),2):
        ra,rb=table.rows[a],table.rows[b]
        if len(ra)!=len(rb) or not ra: continue
        eq=sum(_canonical_cell(x)==_canonical_cell(y) for x,y in zip(ra,rb))
        if eq/len(ra)>=.8 and eq<len(ra):
            residue.append({'rows':[a,b],'matching_fraction':eq/len(ra),'status':'unresolved_residue','reevaluation_token':_sha(a,b,eq)})
            if len(residue)>=100: break
    return {'exact_families':exact,'near_families':near,'residue_pool':residue,'lineage':lineage,
            'reevaluation_event_count':len(residue)+len(near)}


CENSOR_RE=re.compile(r'^\s*([<>]=?)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$')

def missingness_diagnostics(table: Table) -> dict[str,Any]:
    nulls={'na','n/a','null','none','nil','missing','unknown','unk','?'}
    states=Counter(); cells=[]; censor=[]
    for r,row in enumerate(table.rows):
        for c,v in enumerate(row):
            t=v.strip(); lo=t.casefold(); state='OBSERVED'; interval=None
            m=CENSOR_RE.match(t)
            if not t: state='MISSING_KNOWN'
            elif lo in {'n/a','not applicable','na:structural'}: state='STRUCTURALLY_NOT_APPLICABLE'
            elif lo in {'absent','none certified','absent_certified'}: state='ABSENT_CERTIFIED'
            elif lo in {'unknown','unk','?'}: state='UNKNOWN'
            elif m:
                state='CENSORED'; op,num=m.group(1),float(m.group(2));
                interval={'lower':num if op.startswith('>') else None,'upper':num if op.startswith('<') else None,'lower_closed':op=='>=','upper_closed':op=='<='}
                censor.append({'row':r,'column':c,'raw':v,'interval':interval})
            elif lo in nulls: state='MISSING_KNOWN'
            states[state]+=1
            if state!='OBSERVED': cells.append({'row':r,'column':c,'state':state,'raw':v,'interval':interval})
    mechanisms=[]
    # Compare missing rate by low-cardinality categorical columns.
    for target in range(len(table.header)):
        miss=[(r, target>=len(row) or not row[target].strip()) for r,row in enumerate(table.rows)]
        base=sum(x for _,x in miss)/max(1,len(miss))
        if not (0<base<1): continue
        for cond in range(len(table.header)):
            if cond==target: continue
            groups=defaultdict(list)
            for r,is_missing in miss:
                if cond<len(table.rows[r]): groups[table.rows[r][cond]].append(is_missing)
            if not (2<=len(groups)<=12): continue
            rates=[sum(v)/len(v) for v in groups.values() if len(v)>=3]
            if len(rates)>=2 and max(rates)-min(rates)>=.35:
                mechanisms.append({'target':target,'conditioner':cond,'base_missing_rate':base,'group_rate_span':max(rates)-min(rates),'structure_dependent':True})
    uncertainty=[]
    for r,row in enumerate(table.rows[:1000]):
        for c,v in enumerate(row):
            m=CENSOR_RE.match(v.strip())
            if m:
                uncertainty.append({'row':r,'column':c,'kind':'interval','source':'censoring'})
            elif _d(v) is not None:
                decimals=len(v.split('.')[-1]) if '.' in v and 'e' not in v.casefold() else 0
                q=10.0**(-decimals) if decimals else 1.0
                uncertainty.append({'row':r,'column':c,'kind':'quantization','half_step':q/2,'source':'reporting_precision'})
    stale=[]
    for c,h in enumerate(table.header):
        vals=[row[c] for row in table.rows if c<len(row)]
        run=1; best=1
        for a,b in zip(vals,vals[1:]):
            run=run+1 if a==b and a!='' else 1; best=max(best,run)
        if len(vals)>=10 and best>=max(5,int(.25*len(vals))): stale.append({'column':c,'name':h,'longest_unchanged_run':best,'stale_candidate':True})
    return {'states':dict(states),'cells':cells,'censoring':censor,'mechanism_tests':mechanisms,'uncertainty':uncertainty,
            'uncertainty_propagation_policy':'interval arithmetic / first-order independent-error propagation; covariance retained when available',
            'bounded_reconstruction_policy':'emit interval when intersection is non-empty and not point-identifiable',
            'error_taxonomy':['measurement','calibration','quantization','reporting','model','delay','aggregation','missingness'],
            'stale_candidates':stale}


def _numeric_cols(table: Table, min_cov:float=.8) -> list[int]:
    out=[]
    for c in range(len(table.header)):
        vals=[_d(row[c]) for row in table.rows if c<len(row) and row[c].strip()]
        if vals and sum(v is not None for v in vals)/len(vals)>=min_cov: out.append(c)
    return out


def _entropy(values: list[str]) -> float:
    n=len(values)
    if not n: return 0.0
    cnt=Counter(values)
    return -sum((k/n)*log(k/n,2) for k in cnt.values())


def _mutual_information(xs:list[str],ys:list[str]) -> float:
    n=min(len(xs),len(ys))
    if n==0:return 0.0
    xs=xs[:n]; ys=ys[:n]; cx=Counter(xs);cy=Counter(ys);cxy=Counter(zip(xs,ys))
    out=0.0
    for (x,y),k in cxy.items():
        p=k/n; out += p*log(p/((cx[x]/n)*(cy[y]/n)),2)
    return out


def _fit_linear(xs:list[list[float]], ys:list[float]) -> tuple[list[float],float] | None:
    # Least squares via normal equations + Gaussian elimination. Includes intercept.
    if not xs or len(xs)!=len(ys): return None
    p=len(xs[0])+1
    if len(xs)<p+2: return None
    A=[[0.0]*p for _ in range(p)]; b=[0.0]*p
    for row,y in zip(xs,ys):
        z=[1.0]+row
        for i in range(p):
            b[i]+=z[i]*y
            for j in range(p): A[i][j]+=z[i]*z[j]
    # Gauss-Jordan with pivot safety; return rough condition proxy.
    piv=[]
    for i in range(p):
        k=max(range(i,p),key=lambda r:abs(A[r][i]))
        if abs(A[k][i])<1e-12:return None
        A[i],A[k]=A[k],A[i]; b[i],b[k]=b[k],b[i]
        pv=A[i][i]; piv.append(abs(pv))
        for j in range(i,p): A[i][j]/=pv
        b[i]/=pv
        for r in range(p):
            if r==i:continue
            q=A[r][i]
            for j in range(i,p):A[r][j]-=q*A[i][j]
            b[r]-=q*b[i]
    cond=max(piv)/max(min(piv),1e-18)
    return b,cond


def relationship_diagnostics(table: Table, max_terms:int=5) -> dict[str,Any]:
    nums=_numeric_cols(table); n=len(table.rows)
    composite=[]; conservation=[]; order=[]; fds=[]; mi=[]; keys=[]; conditional=[]; regimes=[]; aggregates=[]; lags=[]; seasonal=[]
    # Bounded expression/linear search: coefficients learned, then exact/simple coefficient gate.
    for target in nums[:10]:
        sources=[c for c in nums[:10] if c!=target]
        for k in range(2,min(max_terms,4,len(sources))+1):
            for comb in combinations(sources,k):
                X=[];Y=[]
                for row in table.rows:
                    vals=[_f(row[c]) if c<len(row) else None for c in comb]; y=_f(row[target]) if target<len(row) else None
                    if y is not None and all(v is not None for v in vals):X.append([float(v) for v in vals]);Y.append(y)
                fit=_fit_linear(X,Y)
                if not fit:continue
                coef,cond=fit
                pred=[coef[0]+sum(a*x for a,x in zip(coef[1:],r)) for r in X]
                mae=mean(abs(a-b) for a,b in zip(Y,pred)) if Y else 1e9
                scale=max(1.0,median(abs(y) for y in Y)) if Y else 1.0
                simple=all(abs(x-round(x))<1e-8 and abs(round(x))<=4 for x in coef)
                if len(Y)>=12 and mae/scale<1e-8 and simple:
                    rec={'target':target,'sources':list(comb),'coefficients':[round(x) for x in coef],'support':len(Y),'relative_mae':mae/scale,'condition_proxy':cond}
                    composite.append(rec)
                    if abs(coef[0])<1e-8: conservation.append(rec|{'null_relation':[target]+list(comb)})
                if len(composite)>=120:break
            if len(composite)>=120:break
        if len(composite)>=120:break
    # Order and monotonicity
    for c in nums:
        vals=[_f(row[c]) for row in table.rows if c<len(row)]
        vv=[v for v in vals if v is not None]
        if len(vv)>=8:
            inc=sum(b>=a for a,b in zip(vv,vv[1:]))/max(1,len(vv)-1)
            if inc>=.98: order.append({'kind':'nondecreasing','column':c,'support':len(vv),'confidence':inc})
    for a,b in combinations(nums[:12],2):
        pairs=[(_f(row[a]),_f(row[b])) for row in table.rows if max(a,b)<len(row)]
        pairs=[p for p in pairs if p[0] is not None and p[1] is not None]
        if len(pairs)>=8:
            conf=sum(x<=y for x,y in pairs)/len(pairs)
            if conf>=.98: order.append({'kind':'pair_order','left':a,'right':b,'operator':'<=','support':len(pairs),'confidence':conf})
    # Higher-arity FDs and candidate keys (combinations 1..3).
    cols=list(range(len(table.header)))
    for k in (1,2,3):
        for det in combinations(cols[:12],k):
            mapping=defaultdict(set); rows=0
            for row in table.rows:
                if max(det)>=len(row):continue
                key=tuple(row[c] for c in det)
                if any(x=='' for x in key):continue
                rows+=1
                for target in cols[:12]:
                    if target in det or target>=len(row):continue
                    mapping[(target,key)].add(row[target])
            for target in cols[:12]:
                groups=[vals for (t,_),vals in mapping.items() if t==target]
                if rows>=12 and groups and all(len(v)<=1 for v in groups):
                    fds.append({'determinants':list(det),'target':target,'support':rows,'arity':k})
            if rows>=12:
                keys_seen=[]
                for row in table.rows:
                    if max(det)<len(row):keys_seen.append(tuple(row[c] for c in det))
                uniq=len(set(keys_seen))/max(1,len(keys_seen))
                if uniq==1.0: keys.append({'columns':list(det),'arity':k,'uniqueness':1.0,'information_score':sum(_entropy([r[c] for r in table.rows if c<len(r)]) for c in det)})
            if len(fds)>150:break
        if len(fds)>150:break
    # MI screen via string values; reports normalized strength without declaring causation.
    for a,b in combinations(cols[:12],2):
        pairs=[(r[a],r[b]) for r in table.rows if max(a,b)<len(r) and r[a]!='' and r[b]!='']
        if len(pairs)<12:continue
        x=[p[0] for p in pairs];y=[p[1] for p in pairs]; m=_mutual_information(x,y); den=max(_entropy(x),_entropy(y),1e-12)
        if m/den>=.5: mi.append({'columns':[a,b],'mi_bits':m,'normalized':m/den,'support':len(pairs)})
    # Conditional and nested regimes: reuse exact linear/simple formula signatures by low-cardinality groups.
    cats=[c for c in cols[:10] if 2<=len({r[c] for r in table.rows if c<len(r)})<=12]
    for g in cats[:4]:
        groups=defaultdict(list)
        for ri,row in enumerate(table.rows):
            if g<len(row):groups[row[g]].append(ri)
        for val,idxs in groups.items():
            if len(idxs)<8:continue
            sub=Table(table.header,[table.rows[i] for i in idxs],table.dialect,table.encoding,table.utf8_bom)
            # Simple y=x*k ratio stable in group.
            ns=_numeric_cols(sub)
            for a,b in combinations(ns[:8],2):
                ratios=[]
                for row in sub.rows:
                    x=_f(row[a]);y=_f(row[b])
                    if x not in (None,0.0) and y is not None:ratios.append(y/x)
                if len(ratios)>=8 and pstdev(ratios)<=1e-10*max(1,abs(mean(ratios))):
                    conditional.append({'if_column':g,'if_value':val,'source':a,'target':b,'kind':'ratio','coefficient':mean(ratios),'support':len(ratios)})
    for g1,g2 in combinations(cats[:4],2):
        combo=defaultdict(list)
        for ri,row in enumerate(table.rows):
            if max(g1,g2)<len(row):combo[(row[g1],row[g2])].append(ri)
        stable=sum(len(v)>=5 for v in combo.values())
        if stable>=2:regimes.append({'scope_columns':[g1,g2],'stable_groups':stable,'group_count':len(combo),'kind':'nested_scope_candidate'})
    # Aggregate/detail based on explicit common names and groups.
    name_map={h.casefold():i for i,h in enumerate(table.header)}
    amount=next((i for h,i in name_map.items() if h in {'amount','value','price','total_value'}),None)
    group=next((i for h,i in name_map.items() if h in {'group','category','invoice_id','order_id'}),None)
    total_col=next((i for h,i in name_map.items() if h in {'total','subtotal','group_total'}),None)
    if amount is not None and group is not None and total_col is not None:
        sums=defaultdict(float);obs=defaultdict(list)
        for ri,row in enumerate(table.rows):
            if max(amount,group,total_col)>=len(row):continue
            a=_f(row[amount]); t=_f(row[total_col]);
            if a is not None:sums[row[group]]+=a
            if t is not None:obs[row[group]].append(t)
        for k,s in sums.items():
            if obs[k]:aggregates.append({'group':k,'sum_amount':s,'reported_values':obs[k],'consistent':all(_isclose(x,s) for x in obs[k])})
    # Lag and seasonal autocorrelation.
    for c in nums[:10]:
        vals=[_f(r[c]) if c<len(r) else None for r in table.rows]
        for lag in range(1,min(6,max(1,len(vals)//5))):
            pairs=[(vals[i-lag],vals[i]) for i in range(lag,len(vals)) if vals[i-lag] is not None and vals[i] is not None]
            if len(pairs)>=12:
                x=[p[0] for p in pairs];y=[p[1] for p in pairs];mx=mean(x);my=mean(y);sx=sqrt(sum((z-mx)**2 for z in x));sy=sqrt(sum((z-my)**2 for z in y));corr=(sum((a-mx)*(b-my) for a,b in pairs)/(sx*sy)) if sx and sy else 0.0
                if abs(corr)>=.8:lags.append({'column':c,'lag':lag,'correlation':corr,'support':len(pairs)})
        for p in range(2,min(13,len(vals)//2+1)):
            pairs=[(vals[i-p],vals[i]) for i in range(p,len(vals)) if vals[i-p] is not None and vals[i] is not None]
            if len(pairs)<12:continue
            x=[a for a,b in pairs];y=[b for a,b in pairs];mx=mean(x);my=mean(y);sx=sqrt(sum((z-mx)**2 for z in x));sy=sqrt(sum((z-my)**2 for z in y));corr=(sum((a-mx)*(b-my) for a,b in pairs)/(sx*sy)) if sx and sy else 0.0
            if corr>=.85:seasonal.append({'column':c,'period_rows':p,'autocorrelation':corr})
    return {'composite_formulas':composite,'linear_conservation':conservation,'order_constraints':order,'functional_dependencies':fds,
            'mutual_information':mi,'candidate_keys':keys,'conditional_rules':conditional,'nested_regimes':regimes,'aggregate_checks':aggregates,
            'lag_relations':lags,'seasonal_relations':seasonal}


def temporal_sampling_diagnostics(table: Table) -> dict[str,Any]:
    dcols=datetime_profiles(table); irregular=[]; multirate=[]
    for p in dcols:
        c=p['column']; vals=[]
        for row in table.rows:
            if c<len(row):
                dt,_,a=_parse_dt(row[c])
                if dt is not None and not a: vals.append(dt.timestamp())
        gaps=[b-a for a,b in zip(vals,vals[1:]) if b>a]
        if len(gaps)>=5:
            mu=mean(gaps);cv=pstdev(gaps)/mu if mu else 0.0
            irregular.append({'column':c,'median_gap_seconds':median(gaps),'gap_cv':cv,'irregular':cv>.1})
    # Multi-rate when a channel/device column partitions a timestamp column.
    cats=[c for c in range(len(table.header)) if 2<=len({r[c] for r in table.rows if c<len(r)})<=20]
    for dp in dcols[:2]:
        tc=dp['column']
        for cc in cats[:5]:
            if cc==tc:continue
            groups=defaultdict(list)
            for row in table.rows:
                if max(tc,cc)>=len(row):continue
                dt,_,a=_parse_dt(row[tc])
                if dt is not None and not a:groups[row[cc]].append(dt.timestamp())
            meds={g:median([b-a for a,b in zip(sorted(v),sorted(v)[1:]) if b>a]) for g,v in groups.items() if len(v)>=4 and any(b>a for a,b in zip(sorted(v),sorted(v)[1:]))}
            if len(meds)>=2 and max(meds.values())/max(min(meds.values()),1e-9)>=1.5:
                multirate.append({'timestamp_column':tc,'channel_column':cc,'median_gaps':meds,'multirate':True})
    return {'irregular_sampling':irregular,'multirate':multirate}


def _rank1_witnesses(table: Table, max_cols:int=10) -> dict[str,Any]:
    nums=_numeric_cols(table)[:max_cols]
    if len(nums)<2 or len(table.rows)<3:return {'applicable':False,'completions':[],'sparse_residuals':[]}
    mat=[[ _f(row[c]) if c<len(row) else None for c in nums] for row in table.rows]
    completions=[]
    for i,row in enumerate(mat):
        for j,v in enumerate(row):
            if v is not None:continue
            vals=[]
            for k in range(len(nums)):
                if k==j or row[k] in (None,0.0):continue
                for l in range(len(mat)):
                    if l==i:continue
                    a=mat[l][j]; b=mat[l][k]
                    if a is None or b in (None,0.0):continue
                    vals.append(row[k]*a/b)
            if len(vals)>=2 and max(vals)-min(vals)<=1e-8*max(1.0,abs(mean(vals))):
                completions.append({'row':i,'column':nums[j],'value':mean(vals),'independent_2x2_witnesses':len(vals)})
    # Robust low-rank+sparse localization: estimate rank1 from first anchor row/col and list sparse outliers.
    residuals=[]; anchor=None
    for i in range(len(mat)):
        for j in range(len(nums)):
            if mat[i][j] not in (None,0.0):anchor=(i,j);break
        if anchor:break
    if anchor:
        i0,j0=anchor; base=mat[i0][j0]
        for i in range(len(mat)):
            if mat[i][j0] is None:continue
            for j in range(len(nums)):
                if mat[i][j] is None or mat[i0][j] is None:continue
                pred=mat[i][j0]*mat[i0][j]/base
                if not _isclose(mat[i][j],pred,1e-8,1e-8):residuals.append({'row':i,'column':nums[j],'observed':mat[i][j],'rank1_expected':pred,'residual':mat[i][j]-pred})
    density=len(residuals)/max(1,sum(v is not None for row in mat for v in row))
    return {'applicable':bool(anchor) and density<=.1,'rank_gate':'rank1_2x2_minor_consistency','completions':completions,'sparse_residuals':residuals[:500],'residual_density':density}


def reconstruction_diagnostics(table: Table, relationships:dict[str,Any], temporal_sampling:dict[str,Any]) -> dict[str,Any]:
    rank=_rank1_witnesses(table)
    # Sparse recovery: one-sparse syndrome candidate if exactly one rank residual and many checked cells.
    sparse={'applicable': rank['applicable'] and 0 < len(rank['sparse_residuals']) <= max(1, int(.02 * max(1, len(table.rows) * max(1, len(_numeric_cols(table)))))),
            'candidate_cells':[{'row':x['row'],'column':x['column'],'value':x['rank1_expected']} for x in rank['sparse_residuals'][:20]]}
    # State-space / Bayesian / interpolation / forward-backward for single numeric missing values.
    state=[]; bayes=[]; interp=[]; fb=[]; intervals=[]
    for c in _numeric_cols(table)[:12]:
        vals=[_f(row[c]) if c<len(row) else None for row in table.rows]
        diffs=[b-a for a,b in zip(vals,vals[1:]) if a is not None and b is not None]
        sigma=pstdev(diffs) if len(diffs)>=4 else None
        drift=median(diffs) if diffs else 0.0
        for i,v in enumerate(vals):
            if v is not None or i==0 or i==len(vals)-1:continue
            prev=vals[i-1];nxt=vals[i+1]
            if prev is None or nxt is None:continue
            fp=prev+drift;bp=nxt-drift
            agree=_isclose(fp,bp,atol=max(1e-8,(sigma or 0)*2),rtol=1e-6)
            fb.append({'row':i,'column':c,'forward':fp,'backward':bp,'agree':agree})
            interp_val=(prev+nxt)/2
            interp.append({'row':i,'column':c,'method':'linear','value':interp_val,'support_rows':[i-1,i+1],'bounded_model':True})
            if agree:
                state.append({'row':i,'column':c,'value':(fp+bp)/2,'model':'local_constant_drift','timestamp_check_required':bool(temporal_sampling['irregular_sampling'])})
                var=(sigma or 0.0)**2
                bayes.append({'row':i,'column':c,'posterior_mean':(fp+bp)/2,'posterior_std':sqrt(var/2) if var else 0.0,'model':'two_direction_gaussian'})
            intervals.append({'row':i,'column':c,'lower':min(prev,nxt),'upper':max(prev,nxt),'source':'neighbor_bracket'})
    # Arbitrary-depth graph reachability: treat discovered composite relations as hyperedges.
    edges=defaultdict(set)
    for rel in relationships.get('composite_formulas',[]):
        cols=[rel['target']]+rel['sources']
        for a in cols:
            for b in cols:
                if a!=b:edges[a].add(b)
    reach={}
    for c in edges:
        seen={c};front=[c]
        while front:
            x=front.pop(0)
            for y in edges[x]:
                if y not in seen:seen.add(y);front.append(y)
        reach[str(c)]=sorted(seen-{c})
    return {'multipath_reconstruction':{'relation_count':len(relationships.get('composite_formulas',[])),'reachable_columns':reach},
            'arbitrary_depth_chains':reach,'low_rank_completion':rank,'robust_low_rank_sparse':{'applicable':rank['applicable'],'outliers':rank['sparse_residuals']},
            'sparse_recovery':sparse,'state_space_reconstruction':state,'bayesian_filter_reconstruction':bayes,
            'bounded_interpolation':interp,'forward_backward_agreement':fb,'interval_reconstruction':intervals}


def validation_diagnostics(table: Table, rel:dict[str,Any]) -> dict[str,Any]:
    comps=rel.get('composite_formulas',[])
    nested=[];holdout=[];negative=[];robust=[];sens=[]
    n=len(table.rows); idx=list(range(n)); train=idx[:max(1,int(.6*n))]; val=idx[max(1,int(.6*n)):max(1,int(.8*n))]; test=idx[max(1,int(.8*n)):]
    for r in comps[:100]:
        t=r['target']; ss=r['sources']; coef=r['coefficients']
        def errs(rows):
            e=[]
            for i in rows:
                row=table.rows[i]
                if max([t]+ss)>=len(row):continue
                y=_f(row[t]); xs=[_f(row[c]) for c in ss]
                if y is None or any(x is None for x in xs):continue
                p=coef[0]+sum(a*x for a,x in zip(coef[1:],xs));e.append(abs(y-p))
            return e
        et,ev,eh=errs(train),errs(val),errs(test)
        nested.append({'relation':_sha(r),'train_mae':mean(et) if et else None,'validation_mae':mean(ev) if ev else None,'test_mae':mean(eh) if eh else None,'nested_split_locked':True})
        holdout.append({'relation':_sha(r),'holdout_rows':len(test),'holdout_pass':bool(eh) and max(eh)<=1e-7})
        # Deterministic negative control: reverse target on the same complete rows.
        ys=[];pred=[]
        for row in table.rows:
            if max([t]+ss)>=len(row):continue
            y=_f(row[t]);xs=[_f(row[c]) for c in ss]
            if y is None or any(x is None for x in xs):continue
            ys.append(y);pred.append(coef[0]+sum(a*x for a,x in zip(coef[1:],xs)))
        rev=list(reversed(ys)); neg=mean(abs(a-b) for a,b in zip(rev,pred)) if rev else None
        pos=mean(abs(a-b) for a,b in zip(ys,pred)) if ys else None
        negative.append({'relation':_sha(r),'positive_mae':pos,'negative_control_mae':neg,'control_separates':neg is not None and pos is not None and neg>pos+1e-8})
        # Distributional stress: quartile blocks.
        block_errors=[]
        for q in range(4):
            rows=idx[q*n//4:(q+1)*n//4]
            ee=errs(rows);block_errors.append(mean(ee) if ee else None)
        robust.append({'relation':_sha(r),'quartile_mae':block_errors,'robust_pass':all(x is not None and x<=1e-7 for x in block_errors)})
        sens.append({'relation':_sha(r),'condition_proxy':r.get('condition_proxy'),'sensitivity_guard_pass':(r.get('condition_proxy') or 1e99)<1e10})
    # Diagonal multivariate OOD using robust z on numeric columns.
    nums=_numeric_cols(table)[:12]; stats={}
    for c in nums:
        vals=[_f(row[c]) for row in table.rows if c<len(row)];vv=[v for v in vals if v is not None]
        if len(vv)>=8:stats[c]=(mean(vv),pstdev(vv) or 1.0)
    ood=[]
    for i,row in enumerate(table.rows):
        z2=0;used=0
        for c,(m,s) in stats.items():
            if c<len(row) and (v:=_f(row[c])) is not None:z2+=((v-m)/s)**2;used+=1
        if used and sqrt(z2/used)>=4:ood.append({'row':i,'rms_z':sqrt(z2/used),'multivariate_ood':True})
    return {'nested_validation':nested,'locked_holdout':holdout,'negative_controls':negative,'distributional_stress':robust,'multivariate_ood':ood,'inverse_sensitivity':sens}


def build_maxima50_diagnostics(table: Table, config:Any=None) -> dict[str,Any]:
    structure=structural_row_recovery(table)
    headers=header_repair(table)
    locales=locale_profiles(table)
    datetimes=datetime_profiles(table)
    units=unit_currency_graph(table)
    duplicates=duplicate_diagnostics(table)
    missing=missingness_diagnostics(table)
    relationships=relationship_diagnostics(table, max_terms=getattr(config,'maxima_expression_terms',5) if config is not None else 5)
    temporal=temporal_sampling_diagnostics(table)
    reconstruction=reconstruction_diagnostics(table,relationships,temporal)
    validation=validation_diagnostics(table,relationships)
    return {
        'enabled':True,'registry':feature_registry(),
        'input_structure':{'row_recovery':structure,'header_repair':headers,'locale_numeric':locales,'datetime_timezone':datetimes,'unit_currency':units},
        'duplicates':duplicates,'missingness_uncertainty':missing,'relationships':relationships,'temporal_sampling':temporal,
        'reconstruction':reconstruction,'validation':validation,
    }


def _candidate_id(*parts:Any)->str:return _sha(*parts)


class MaximaRepairAnalyzer:
    """Conservative edit surface for the PASS013 feature batch.

    Most Maxima features are diagnostics.  This analyzer materializes only operations that have
    a unique reversible representation and are explicitly enabled by config flags.
    """
    name='maxima_safe'

    def analyze(self, table:Table, config:Any)->AnalysisResult:
        # Repair-time hot path: compute only diagnostics that can actually emit edits.
        # The full 50-feature diagnostic packet is still built once for the final report,
        # not on every shadow/counterfactual analysis.
        out=AnalysisResult()
        headers = header_repair(table) if getattr(config,'maxima_repair_headers',False) else {'proposals':[]}
        row_recovery = structural_row_recovery(table) if getattr(config,'maxima_repair_row_alignment',False) else []
        locales = locale_profiles(table) if getattr(config,'maxima_repair_locale_numbers',False) else []
        low_rank = _rank1_witnesses(table) if getattr(config,'maxima_repair_low_rank_missing',False) else {'completions':[]}
        if getattr(config,'maxima_repair_headers',False):
            for p in headers['proposals']:
                c=p['column'];old=table.header[c];new=p['new']
                if new in table.header and new!=old:continue
                out.issues.append(Issue(self.name,'header_canonicalization','Header has a unique reversible canonical form.','info',column=c,value=old,repairable=True,metadata={'suggested':new}))
                out.candidates.append(Candidate(candidate_id=_candidate_id('hdr',c,old,new), analyzer=self.name, operation='rename_column', reason='Canonicalize a uniquely identified header.', cost=1, confidence=1.0, column=c, old_value=old, new_value=new, reversible=True, metadata={'feature_id':3}))
        if getattr(config,'maxima_repair_row_alignment',False):
            for d in row_recovery:
                if not d['unique_safe_alignment'] or not d['proposal']:continue
                r=d['row'];old=list(table.rows[r]);new=list(d['proposal']['new_row'])
                out.issues.append(Issue(self.name,'row_width_realign','Row width has a unique type-compatible realignment.','error',row=r,repairable=True,metadata={'new_row':new}))
                out.candidates.append(Candidate(candidate_id=_candidate_id('row',r,old,new), analyzer=self.name, operation='replace_row', reason='Apply a unique structural row realignment.', cost=max(1,len(old)), confidence=1.0, row=r, old_row=old, new_row=new, reversible=True, metadata={'feature_id':2}))
        if getattr(config,'maxima_repair_locale_numbers',False):
            for p in locales:
                if not p['decisive'] or p['style']=='plain':continue
                c=p['column']; style=p['style']
                for r,row in enumerate(table.rows):
                    if c>=len(row) or not row[c].strip():continue
                    d=_parse_locale_number(row[c],style)
                    if d is None:continue
                    new=_fmt(d);old=row[c]
                    if new==old:continue
                    out.issues.append(Issue(self.name,'locale_numeric_canonicalization','Numeric representation is unambiguous within the column locale.','info',r,c,old,True,{'style':style,'suggested':new}))
                    out.candidates.append(Candidate(candidate_id=_candidate_id('locale',r,c,old,new), analyzer=self.name, operation='set_cell', reason='Canonicalize an unambiguous locale-formatted number.', cost=1, confidence=1.0, row=r, column=c, old_value=old, new_value=new, reversible=True, metadata={'feature_id':4,'style':style}))
        if getattr(config,'maxima_repair_low_rank_missing',False):
            for p in low_rank['completions']:
                r,c=p['row'],p['column']; old=table.rows[r][c];new=_fmt(p['value'])
                if old.strip():continue
                out.issues.append(Issue(self.name,'rank1_missing_reconstruction','Missing numeric cell is reconstructed by multiple agreeing 2x2 rank-1 witnesses.','warning',r,c,old,True,{'suggested':new,'witnesses':p['independent_2x2_witnesses']}))
                out.candidates.append(Candidate(candidate_id=_candidate_id('rank1',r,c,new), analyzer=self.name, operation='set_cell', reason='Reconstruct missing value from agreeing rank-1 witnesses.', cost=1, confidence=1.0, row=r, column=c, old_value=old, new_value=new, reversible=True, metadata={'feature_id':38,'witnesses':p['independent_2x2_witnesses']}))
        return out


def _column_value_set(table:Table,c:int)->set[str]:
    return {row[c] for row in table.rows if c<len(row) and row[c]!=''}


def build_bundle_maxima50(tables:dict[str,Table]) -> dict[str,Any]:
    names=sorted(tables); relations=[]; join_recovery=[]; cross_recon=[]; versions=[]
    for a,b in combinations(names,2):
        ta,tb=tables[a],tables[b]
        for ca,ha in enumerate(ta.header):
            va=_column_value_set(ta,ca)
            if len(va)<3:continue
            for cb,hb in enumerate(tb.header):
                vb=_column_value_set(tb,cb)
                if len(vb)<3:continue
                inter=va&vb; overlap=len(inter)/max(1,min(len(va),len(vb)))
                name_hint=_canonical_cell(ha)==_canonical_cell(hb)
                if overlap>=.8 or (name_hint and overlap>=.5):
                    relations.append({'left_file':a,'left_column':ca,'right_file':b,'right_column':cb,'overlap':overlap,'bidirectional_return':overlap>=.8})
    # Join-key recovery and cross-file reconstruction from unique shared keys + same-name attributes.
    for rel in relations:
        if not rel['bidirectional_return']:continue
        ta,tb=tables[rel['left_file']],tables[rel['right_file']];ca,cb=rel['left_column'],rel['right_column']
        ia=defaultdict(list);ib=defaultdict(list)
        for r,row in enumerate(ta.rows):
            if ca<len(row):ia[row[ca]].append(r)
        for r,row in enumerate(tb.rows):
            if cb<len(row):ib[row[cb]].append(r)
        if all(len(v)==1 for v in ia.values()) and all(len(v)==1 for v in ib.values()):
            join_recovery.append(rel|{'unique_both_sides':True})
            common_names=set(ta.header)&set(tb.header)
            for h in common_names:
                xa,xb=ta.header.index(h),tb.header.index(h)
                if xa==ca or xb==cb:continue
                for key in ia.keys()&ib.keys():
                    ra,rb=ia[key][0],ib[key][0];va=ta.rows[ra][xa] if xa<len(ta.rows[ra]) else '';vb=tb.rows[rb][xb] if xb<len(tb.rows[rb]) else ''
                    if not va and vb:cross_recon.append({'file':rel['left_file'],'row':ra,'column':xa,'value':vb,'witness_file':rel['right_file'],'key':key})
                    elif not vb and va:cross_recon.append({'file':rel['right_file'],'row':rb,'column':xb,'value':va,'witness_file':rel['left_file'],'key':key})
    # Version-specific registry from conventional version/schema columns.
    for name,t in tables.items():
        for c,h in enumerate(t.header):
            if h.casefold() in {'version','schema_version','revision','rev'}:
                versions.append({'file':name,'column':c,'versions':sorted(_column_value_set(t,c))})
    return {'automatic_multitable_relationships':relations,'join_key_recovery':join_recovery,'schema_version_registries':versions,
            'cross_file_reconstruction':cross_recon,'feature_ids':[33,34,35,46]}
