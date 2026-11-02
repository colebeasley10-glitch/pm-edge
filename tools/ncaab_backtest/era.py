"""Second, era-aware pass: calibrate only on the most recent pre-test seasons (2020-2022), split
neutral-site vs true-home games, then apply to untouched 2023 + 2026."""
from analysis import *
import json
df=prep(load('v1'))
va=df[df.season.isin(VAL)]; te=df[df.season.isin(TEST)]
out={}
cal={}
for nm,m_va,m_te in (('home-court',~va.neutral,~te.neutral),('neutral',va.neutral,te.neutral)):
    c=calibrate(va[m_va]); cal[nm]=c
    print(nm,'VAL calibration',{k:round(v,3) for k,v in c.items()})
    print('   TEST calibration (for reference only)',{k:round(v,3) for k,v in calibrate(te[m_te]).items()})
    for tau in (0.0,0.02,0.03,0.05):
        B=bets(te[m_te],c,tau,tau,tau)
        s=summ(B,'market'); print('  tau',tau); print(s.to_string())
        out[f'{nm}_tau{tau}']=s.reset_index().to_dict('records')
# leave-one-season-out inside VAL for the neutral hypothesis (is it stable year to year?)
for S in VAL:
    c=calibrate(va[va.neutral&(va.season!=S)]); B=bets(va[va.neutral&(va.season==S)],c,0.03,9,9)
    print('LOSO neutral',S,summ(B[B.market=='SPREAD']).to_dict('records'))
json.dump({'cal':cal,'test':out},open(D+'era_results.json','w'),indent=1,default=str)
