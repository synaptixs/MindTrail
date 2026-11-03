"""Scoped, bounded current daily discovery access and exact source inspection."""
from __future__ import annotations
import re
import sqlite3
from analysis_provenance import fingerprint
from discovery_daily import MAX_DAY_BYTES, selection
from exploration_access import verified_waveform

HEX=re.compile(r'[0-9a-f]{64}')
DATE=re.compile(r'\d{4}-\d{2}-\d{2}')


def validate_index(value,participant,analysis_identity):
    if (value.get('version')!=1 or value.get('participantId')!=participant
            or value.get('analysisIdentity')!=analysis_identity
            or value.get('dailyIdentity')!=fingerprint({k:v for k,v in value.items() if k!='dailyIdentity'})):
        raise ValueError('Invalid daily discovery index')
    return value


def read(manager,participant,group=None,date=None):
    from analysis_manager import read_json
    result={'status':'unavailable','participantId':participant}
    if not participant:return dict(result,status='no_participant')
    if group is not None and (not isinstance(group,str) or not HEX.fullmatch(group)):raise ValueError('Choose a valid acquisition context')
    if date is not None and (not isinstance(date,str) or not DATE.fullmatch(date)):raise ValueError('Choose a valid date')
    if (group is None)!=(date is None):raise ValueError('Choose both context and date')
    with manager.lock:
        try:
            pointer=read_json(manager.root/(manager.scope(participant)+'.json'))
            signature=pointer['signature']
            if signature!=manager.signature():return dict(result,status='stale')
            directory=manager.directory(pointer['id'])
            identity=read_json(directory/'summary.json')['analysisIdentity']
            index=validate_index(read_json(directory/'discovery-daily-index.json'),participant,identity)
            if group is None:value=index
            else:
                context=next((g for g in index['contexts'] if g['acquisitionGroupId']==group),None)
                entry=next((d for d in context['days'] if d['date']==date),None) if context else None
                if entry is None:raise ValueError('Context/date is outside the current daily report')
                value=read_json(directory/f'discovery-day-{group}-{date}.json',limit=MAX_DAY_BYTES)
                if (fingerprint(value)!=entry['payloadHash'] or value['version']!=1 or value['participantId']!=participant
                        or value['analysisIdentity']!=identity or value['discoveryIdentity']!=index['discoveryIdentity']
                        or value['acquisitionGroupId']!=group or value['acquisition']!=context['acquisition'] or value['date']!=date):
                    raise ValueError('Invalid daily discovery payload')
            if signature!=manager.signature():return dict(result,status='stale')
            return dict(value,status='current',sourceSignature=signature,dailyIdentity=index['dailyIdentity'])
        except (OSError,ValueError,KeyError,TypeError):return result


def page(manager,participant,params):
    group,date=params.get('group'),params.get('date')
    payload=read(manager,participant,group,date)
    if payload['status']!='current' or group is None:return payload
    def bound(name):
        value=params.get(name)
        if value is None:return None
        if isinstance(value,bool):raise ValueError('Invalid daily range')
        try:
            if str(int(value))!=str(value):raise ValueError()
            return int(value)
        except (TypeError,ValueError):raise ValueError('Invalid daily range') from None
    result=selection(payload,bound('startMs'),bound('endMs'))
    if payload['sourceSignature']!=manager.signature():return {'status':'stale','participantId':participant}
    return result


def waveform(manager,participant,params):
    payload=read(manager,participant,params.get('group'),params.get('date'))
    window=params.get('window')
    if (payload['status']!='current' or 'windows' not in payload
            or params.get('identity')!=payload['dailyIdentity']):raise ValueError('Daily discovery is stale or unavailable; refresh analysis')
    if not isinstance(window,str) or not HEX.fullmatch(window):raise ValueError('Choose a valid source example')
    row=next((w for w in payload['windows'] if w['windowId']==window),None)
    if row is None:raise ValueError('Window is outside the selected day/context')
    try:value=verified_waveform(manager.database,participant,payload['acquisition'],row)
    except sqlite3.Error:raise ValueError('Exact source waveform is unavailable; rerun analysis') from None
    if payload['sourceSignature']!=manager.signature():raise ValueError('Source changed; refresh analysis')
    return dict(value,qualityFlags=row['qualityFlags'])


def validate_files(directory,participant,analysis_identity,check=lambda:None):
    from analysis_manager import read_json
    index=validate_index(read_json(directory/'discovery-daily-index.json'),participant,analysis_identity)
    for context in index['contexts']:
        group=context['acquisitionGroupId']
        if not isinstance(group,str) or not HEX.fullmatch(group):raise ValueError('Invalid daily context')
        for day in context['days']:
            check();date=day['date']
            if not isinstance(date,str) or not DATE.fullmatch(date):raise ValueError('Invalid daily date')
            value=read_json(directory/f'discovery-day-{group}-{date}.json',limit=MAX_DAY_BYTES)
            if (fingerprint(value)!=day['payloadHash'] or value['version']!=1 or value['discoveryIdentity']!=index['discoveryIdentity'] or value['participantId']!=participant
                    or value['analysisIdentity']!=analysis_identity or value['acquisition']!=context['acquisition']
                    or value['acquisitionGroupId']!=group or value['date']!=date):raise ValueError('Incomplete daily discovery payload')
