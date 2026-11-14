"""Zero-inference campaign packaging; publication remains in Ad Manager/Metricool.
Run: python tools/campaign_batch.py --destination HTTPS_URL --out OUTPUT_DIR
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from PIL import Image, ImageDraw, ImageFont

SLIDES = [
 ('Sales are not profit', 'Before booking a craft fair, count every cost.'),
 ('Count the cash costs', 'Stall fee + travel + materials + packaging + payment fees.'),
 ('Then count your time', 'Setup, selling and packing hours affect whether an event is worth repeating.'),
 ('Compare before booking', 'Use the calculator. Save your assumptions. Compare events using the same inputs.'),
]

def tracked(url, platform):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('A real public HTTPS destination is required')
    if parts.hostname in {'localhost','example.com','127.0.0.1'} or parts.hostname.endswith('.invalid'):
        raise ValueError('Placeholder or local destination is not publishable')
    query = [(k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith('utm_')]
    query += [('utm_source',platform),('utm_medium','organic_social'),('utm_campaign','craft-fair-cost-check')]
    return urlunsplit(parts._replace(query=urlencode(query)))

def package(destination, out, affiliate=False, approved=False, video=True):
    if affiliate and not approved: raise ValueError('Affiliate approval and exact network-issued link must be verified first')
    # Referral URLs may be signed. Never append arbitrary tracking to affiliate links.
    tracked(destination, 'validation')
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    posts = []
    disclosure = 'Ad / affiliate link: I may earn a commission if you buy. ' if affiliate else ''
    for platform in ('pinterest','facebook','instagram','youtube','tiktok'):
        link = destination if affiliate else tracked(destination, platform)
        copy = disclosure + 'A busy stall can still lose money. Count stall fees, travel, materials and your time before booking your next craft fair.'
        if platform in ('pinterest','facebook'): copy += ' Compare your costs: ' + link
        else: copy += ' Use the calculator through the link in the profile.'
        posts.append({'platform':platform,'caption':copy,'destination':link,'status':'prepared',
          'publication_id':None,'published_url':None,'clicks':None,'conversions':None,
          'required':['verified audience fit','reachable destination','public media URL'] + (['public board_id'] if platform=='pinterest' else [])})
    manifest = {'campaign':'craft-fair-cost-check','audience':'craft fair sellers comparing event costs',
      'affiliate':affiliate,'status':'prepared_not_published','posts':posts,
      'income':{'pending':None,'confirmed':None,'received':None,'currency':None},
      'video':{'made_for_kids':False,'title':'Craft fair sales are not profit','audio':'none; original on-screen captions'}}
    font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    files = []
    for i,(title,body) in enumerate(SLIDES,1):
        image = Image.new('RGB',(720,1280),'#17232e'); draw = ImageDraw.Draw(image)
        draw.rectangle((50,90,670,105),fill='#66e3c4')
        draw.multiline_text((50,200),'\n'.join(textwrap.wrap(title,18)),font=ImageFont.truetype(font,58),fill='white',spacing=15)
        draw.multiline_text((50,560),'\n'.join(textwrap.wrap(body,27)),font=ImageFont.truetype(font,36),fill='#dce9ed',spacing=14)
        draw.text((50,1160),f'ADG | Event cost check  {i}/4',font=ImageFont.truetype(font,24),fill='#66e3c4')
        path=out/f'slide-{i:02}.png'; image.save(path); files.append(path)
    manifest['renderer_payload']={'format':'portrait','title':'Craft fair cost check',
      'slides':[{'headline':title,'body':body,'footer':'ADG | Event cost check'} for title,body in SLIDES]}
    if video and shutil.which('ffmpeg'):
        subprocess.run(['ffmpeg','-y','-framerate','1/3','-i',str(out/'slide-%02d.png'),'-vf','fps=25,format=yuv420p','-c:v','libx264','-movflags','+faststart',str(out/'short.mp4')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
        files.append(out/'short.mp4')
    manifest['assets']=[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    (out/'campaign.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--destination',required=True); parser.add_argument('--out',required=True)
    args=parser.parse_args(); package(args.destination,args.out)
