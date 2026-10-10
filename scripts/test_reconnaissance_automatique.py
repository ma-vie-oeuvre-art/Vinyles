"""Regression checks for automatic cover-title recognition."""
import json
import os
import comparer_pochettes as c

def source(regions):
    return {'Transcription source':'[OCR neural regions] '+json.dumps(regions)}
def region(text,height):
    return {'text':text,'height':height,'confidence':.9,'y':0}

f=source([region('SURNAME',100),region('Long Main Title',70),region('Secondary Songs',15)])
good={'titre':'Given Surname - Long Main Title','release_id':1}
bad={'titre':'Given Surname - Secondary Songs','release_id':2}
assert c.main_heading(f,[good,bad])==good
other={'titre':'Other Surname - Long Main Title','release_id':3}
assert c.main_heading(f,[good,other]) is None
assert c.main_heading(f,[{'titre':'Surname - Surname'}]) is None
movie=source([region('ZOO1ASPAaE @DYSSEY',60),region('VOEUME TWC',25)])
right={'titre':'Various - 2001 A Space Odyssey Volume 2','release_id':4}
wrong={'titre':'Various - 2001 A Space Odyssey Volume 1','release_id':5}
assert c.main_heading(movie,[right,wrong])==right
assert c.main_heading(movie,[wrong]) is None
assert 'ALWAYS' in c.expanded_words('ZAlways In')
os.environ['DISCOGS_TOKEN']='test-only'
c.request_json=lambda *a,**k: {'id':99,'title':'Series - Volume Two','artists_sort':'Various','artists':[{'name':'Various'}]}
c.time.sleep=lambda _:None
fields=c.canonical_fields({'release_id':99})
assert fields['Artiste']=='Various'
assert fields['Titre']=='Series - Volume Two'
assert 'Discogs release_id' not in fields
assert fields['Discogs référence release_id']=='99'
print('Regression checks passed: main heading, artist ambiguity, volume, OCR separation, structured identity and deferred pressing.')
