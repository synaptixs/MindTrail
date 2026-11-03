"""Scoped stdlib access to sealed review facts; no annotation inference."""
from analysis_provenance import fingerprint


def validate(value,participant,identity):
    if value.get('version')!=1 or value.get('participantId')!=participant or value.get('analysisIdentity')!=identity or value.get('reviewIdentity')!=fingerprint({k:v for k,v in value.items() if k!='reviewIdentity'}):raise ValueError('Invalid discovery review snapshot')
    if len(value['items'])>24 or len({r['windowId'] for r in value['items']})!=len(value['items']):raise ValueError('Invalid review items')
    return value


def read(manager,participant):
    from analysis_manager import read_json
    result={'status':'unavailable','participantId':participant}
    if not participant:return dict(result,status='no_participant')
    with manager.lock:
        try:
            pointer=read_json(manager.root/(manager.scope(participant)+'.json'));signature=pointer['signature']
            if signature!=manager.signature():return dict(result,status='stale')
            directory=manager.directory(pointer['id']);identity=read_json(directory/'summary.json')['analysisIdentity']
            value=validate(read_json(directory/'discovery-review.json'),participant,identity)
            if signature!=manager.signature():return dict(result,status='stale')
            return dict(value,status='current',sourceSignature=signature)
        except (OSError,KeyError,ValueError,TypeError):return result


def page(manager,participant,cues=False):
    if type(cues) is not bool:raise ValueError('Invalid cues choice')
    value=read(manager,participant)
    if value['status']!='current':return value
    items=[]
    for row in value['items']:
        if not cues and 'ordinary' not in row['streams']:continue
        items.append(dict(row) if cues else dict(row,streams=['ordinary'],reasons=[r for r in row['reasons'] if r['kind']=='ordinary']))
    if not cues:items.sort(key=lambda r:r['ordinaryRank'])
    return {k:v for k,v in value.items() if k not in ('items','recurrenceQueries')}|{'items':items,'shown':len(items),'cues':cues,'hiddenOffered':len(value['items'])-len(items)}


def require(manager,participant,body):
    value=read(manager,participant)
    if value['status']!='current' or value['reviewIdentity']!=body.get('reviewIdentity'):raise ValueError('Review source changed. Refresh analysis.')
    row=next((r for r in value['items'] if r['windowId']==body.get('windowId')),None)
    if row is None:raise ValueError('Choose a current offered review window')
    return value,row


def waveform(manager,participant,params):
    value,row=require(manager,participant,params)
    from discovery_daily_access import waveform as exact
    result=exact(manager,participant,{'group':row['acquisitionGroupId'],'date':row['date'],'window':row['windowId'],'identity':value['dailyIdentity']})
    if value['sourceSignature']!=manager.signature():raise ValueError('Source changed. Refresh analysis.')
    return result
