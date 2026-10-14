"""Vercel/Postgres port of unihub/features.py: community features, messaging,
reactions and access checks. Business logic is an intentional line-for-line
match with the original -- only the schema-bootstrap function is gone, since
on this deployment the full schema (schema_postgres.sql) is applied once up
front instead of being migrated incrementally onto a long-lived SQLite file.
"""
import json, re, time, datetime, hashlib, hmac, os, base64
s = None
KINDS = {'students','roommates','events','lost','opportunities','discounts','clubs','groups'}

def public(u):
    return {'id':u['id'],'name':s.short_name(u['first_name'],u['last_name']),'university':u['university'],'bio':u['bio'],'interests':u['interests']}

def notify(c, uid, text, href):
    c.execute('INSERT INTO notifications(user_id,text,href,created_at) VALUES(?,?,?,?)',(uid,text,href,int(time.time())))

def data(c, me):
    users={u['id']:public(u) for u in c.execute('SELECT * FROM users')}
    following={r[0] for r in c.execute('SELECT target_id FROM follows WHERE user_id=?',(me,))}
    liked={r[0] for r in c.execute('SELECT post_id FROM reactions WHERE user_id=?',(me,))}
    comments=[dict(r,name=users[r['user_id']]['name'],mine=r['user_id']==me) for r in c.execute('SELECT * FROM comments ORDER BY id')]
    posts=[]
    for r in c.execute('SELECT * FROM posts WHERE user_id IS NOT NULL ORDER BY sort_key DESC,id DESC'):
        p=s.post_json(r,me)
        p.update(user_id=r['user_id'],name=users[r['user_id']]['name'],liked=r['id'] in liked,followed=r['user_id'] in following,
                 likes=c.execute('SELECT count(*) FROM reactions WHERE post_id=?',(r['id'],)).fetchone()[0], comments=sum(x['post_id']==r['id'] for x in comments))
        posts.append(p)
    products=[]
    for r in c.execute('SELECT * FROM products WHERE user_id IS NOT NULL ORDER BY id DESC'):
        p=s.product_json(r,me);p.update(user_id=r['user_id'],seller=users[r['user_id']]['name'],image=r['image'],status=r['status']);products.append(p)
    result={k:[] for k in KINDS}
    for r in c.execute('SELECT * FROM entries ORDER BY id DESC'):
        p=json.loads(r['payload']);p.update(id=r['id'],user_id=r['user_id'],university=r['university'],mine=r['user_id']==me,owner=users[r['user_id']]['name'],created_at=r['created_at'])
        if r['kind'] in ('students','roommates'): p.update(name=users[r['user_id']]['name'],tone='blue')
        rs=[dict(x) for x in c.execute('SELECT user_id,status FROM responses WHERE entry_id=?',(r['id'],))]
        p['response']=next((x['status'] for x in rs if x['user_id']==me),'');p['response_count']=len(rs)
        p['participants']=[dict(users[x['user_id']],status=x['status']) for x in rs] if p['mine'] or r['kind'] in ('groups','clubs') else []
        result[r['kind']].append(p)
    messages=[dict(r) for r in c.execute('SELECT * FROM messages WHERE sender_id=? OR recipient_id=? ORDER BY id',(me,me))]
    result.update(posts=posts,products=products,comments=comments,users=list(users.values()),following=list(following),messages=messages,
                  notifications=[dict(r) for r in c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100',(me,))],
                  counts={u:c.execute('SELECT count(*) FROM users WHERE university=?',(u,)).fetchone()[0] for u in s.UNIVERSITIES})
    return result

def text(d,k,limit=500): return s.need_str(d,k,limit)
def choice(d,k,values):
    if d.get(k) not in values: raise s.Bad('Choose a valid '+k,k)
    return d[k]
def number(d,k):
    v=d.get(k)
    if isinstance(v,bool) or not re.fullmatch(r'\d{1,8}',str(v)): raise s.Bad('Enter a whole number for '+k,k)
    v=int(v)
    if v>10000000: raise s.Bad(k+' is too large',k)
    return v

def payload(kind,d):
    fields={'students':['course','year','format','bio'], 'roommates':['district','smoking','pets','lifestyle','sleep','bio'],
            'events':['name','description','date','time','venue','category'], 'lost':['name','description','type','venue'],
            'opportunities':['name','description','category','deadline'], 'discounts':['name','description','code','expires'],
            'clubs':['name','description'],'groups':['name','description','subject','format']}[kind]
    p={k:text(d,k,2000 if k in ('description','bio') else 100) for k in fields}
    if kind in ('students','groups'):p['format']=choice(d,'format',{'Online','Offline'})
    if kind=='students':
        p['subjects']=[x.strip() for x in text(d,'subjects',200).split(',') if x.strip()]
        p['year']=choice(d,'year',{'1st year','2nd year','3rd year','4th year','Graduate'})
    if kind=='roommates':
        p['budget']=number(d,'budget')
        for k,vals in {'smoking':{'Non-smoker','Smoker'},'pets':{'No pets','Pet-friendly'},'lifestyle':{'Quiet','Social','Balanced'},'sleep':{'Early bird','Night owl','Flexible'}}.items():p[k]=choice(d,k,vals)
    if kind=='lost':p['type']=choice(d,'type',{'Lost','Found'})
    for k in ('date','deadline','expires'):
        if k in p:
            try: datetime.date.fromisoformat(p[k])
            except ValueError: raise s.Bad('Enter a valid '+k,k)
    if kind=='events' and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',p['time']):raise s.Bad('Enter a valid time','time')
    p['status']=choice(d,'status',{'Open','Closed'}) if 'status' in d else 'Open'
    return p

def post(path,d,me):
    uid=me['id'];now=int(time.time())
    with s.connect() as c:
        if path=='/api/profile':
            first=text(d,'first_name',50);last=text(d,'last_name',50)
            if not s.NAME_OK(first) or not s.NAME_OK(last):raise s.Bad('Names may contain letters, spaces, hyphens')
            university=choice(d,'university',s.UNIVERSITIES)
            email=d.get('email','')
            if not isinstance(email,str) or len(email)>120 or (email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email)):raise s.Bad('Enter a valid email','email')
            bio=d.get('bio','');interests=d.get('interests','')
            if not isinstance(bio,str) or len(bio)>2000 or not isinstance(interests,str) or len(interests)>200:raise s.Bad('Profile text is too long')
            c.execute('UPDATE users SET first_name=?,last_name=?,university=?,email=?,bio=?,interests=? WHERE id=?',(first,last,university,email,bio,interests,uid))
            return 200,s.user_json(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
        if path=='/api/community':
            c.execute('UPDATE users SET community=? WHERE id=?',(choice(d,'university',s.UNIVERSITIES),uid));return 200,{'ok':True}
        if path=='/api/password':
            if not isinstance(d.get('current_password'),str) or len(d['current_password'])>128:raise s.Bad('Invalid current password')
            if not hmac.compare_digest(s.hash_pw(d['current_password'],me['pw_salt']),me['pw_hash']):raise s.Bad('Current password is incorrect')
            pw=d.get('password')
            if not isinstance(pw,str) or not 8<=len(pw)<=128:raise s.Bad('Password must have at least 8 characters')
            salt=os.urandom(16);c.execute('UPDATE users SET pw_salt=?,pw_hash=? WHERE id=?',(salt,s.hash_pw(pw,salt),uid))
            c.execute('DELETE FROM sessions WHERE user_id=?',(uid,));return 200,{'ok':True}
        if path=='/api/notifications/read':
            c.execute('UPDATE notifications SET read_at=? WHERE user_id=?',(now,uid));return 200,{'ok':True}
        if path=='/api/messages/read':
            other=number(d,'user_id');c.execute('UPDATE messages SET read_at=? WHERE recipient_id=? AND sender_id=?',(now,uid,other));return 200,{'ok':True}
        if path=='/api/messages':
            other=number(d,'user_id');body=text(d,'text',4000)
            if other==uid or not c.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone():raise s.Bad('Recipient not found')
            cur=c.execute('INSERT INTO messages(sender_id,recipient_id,text,created_at) VALUES(?,?,?,?)',(uid,other,body,now))
            notify(c,other,me['first_name']+' sent a message','messages.html?user='+str(uid));return 201,{'id':cur.lastrowid}
        if path=='/api/follow':
            other=number(d,'user_id')
            if other==uid or not c.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone():raise s.Bad('User not found')
            if c.execute('SELECT 1 FROM follows WHERE user_id=? AND target_id=?',(uid,other)).fetchone():c.execute('DELETE FROM follows WHERE user_id=? AND target_id=?',(uid,other))
            else:c.execute('INSERT INTO follows VALUES(?,?)',(uid,other))
            return 200,{'ok':True}
        m=re.fullmatch(r'/api/posts/(\d+)/(like|comments|edit)',path)
        if m:
            pid=int(m[1]);p=c.execute('SELECT * FROM posts WHERE id=? AND user_id IS NOT NULL',(pid,)).fetchone()
            if not p:raise s.Bad('Post not found',code=404)
            if m[2]=='like':
                if c.execute('SELECT 1 FROM reactions WHERE user_id=? AND post_id=?',(uid,pid)).fetchone():c.execute('DELETE FROM reactions WHERE user_id=? AND post_id=?',(uid,pid))
                else:c.execute('INSERT INTO reactions VALUES(?,?)',(uid,pid))
            elif m[2]=='edit':
                if p['user_id']!=uid:raise s.Bad('Only the author can edit this post',code=403)
                c.execute('UPDATE posts SET text=?,category=? WHERE id=?',(text(d,'text',500),choice(d,'category',s.CATEGORIES_POST),pid))
            else:
                c.execute('INSERT INTO comments(user_id,post_id,text,created_at) VALUES(?,?,?,?)',(uid,pid,text(d,'text',2000),now))
                if p['user_id']!=uid:notify(c,p['user_id'],me['first_name']+' replied to your post','feed.html?post='+str(pid))
            return 200,{'ok':True}
        m=re.fullmatch(r'/api/products/(\d+)/edit',path)
        if m:
            if not c.execute('SELECT 1 FROM products WHERE id=? AND user_id=?',(int(m[1]),uid)).fetchone():raise s.Bad('Listing not found',code=404)
            image=d.get('image','')
            if not isinstance(image,str) or len(image)>2800000:raise s.Bad('Image must be smaller than 2 MB')
            if image:
                mm=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',image)
                if not mm:raise s.Bad('Upload a PNG, JPEG or WebP image')
                try: raw=base64.b64decode(mm[2],validate=True)
                except ValueError:raise s.Bad('Invalid image')
                if len(raw)>2000000 or not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw[:4]==b'RIFF' and raw[8:12]==b'WEBP')):raise s.Bad('Invalid image')
            c.execute('UPDATE products SET name=?,price=?,category=?,condition=?,status=?,image=? WHERE id=?',(text(d,'name',80),number(d,'price'),choice(d,'category',s.CATEGORIES_PRODUCT),text(d,'condition',40),choice(d,'status',{'Available','Sold'}),image,int(m[1])))
            return 200,{'ok':True}
        m=re.fullmatch(r'/api/entries/([a-z]+)(?:/(\d+))?',path)
        if m:
            kind=m[1]
            if kind not in KINDS:raise s.Bad('Unknown section',code=404)
            p=payload(kind,d);uni=choice(d,'university',s.UNIVERSITIES)
            if m[2]:
                cur=c.execute('UPDATE entries SET payload=?,university=? WHERE id=? AND user_id=? AND kind=?',(json.dumps(p),uni,int(m[2]),uid,kind))
                if not cur.rowcount:raise s.Bad('Record not found',code=404)
                eid=int(m[2])
            else:
                if kind in ('students','roommates') and c.execute('SELECT 1 FROM entries WHERE user_id=? AND kind=?',(uid,kind)).fetchone():raise s.Bad('You already have a profile here. Edit the existing profile.',code=409)
                eid=c.execute('INSERT INTO entries(kind,user_id,university,payload,created_at) VALUES(?,?,?,?,?)',(kind,uid,uni,json.dumps(p),now)).lastrowid
            return 200,{'id':eid}
        m=re.fullmatch(r'/api/entries/(\d+)/response',path)
        if m:
            eid=int(m[1]);r=c.execute('SELECT * FROM entries WHERE id=?',(eid,)).fetchone()
            if not r:raise s.Bad('Record not found',code=404)
            allowed={'events':{'Interested','Going'},'groups':{'Joined'},'clubs':{'Joined'},'opportunities':{'Applied'},'discounts':{'Saved'}}
            if r['kind'] not in allowed:raise s.Bad('This record does not accept responses')
            status=choice(d,'status',allowed[r['kind']]|{''})
            p=json.loads(r['payload'])
            expires=p.get('deadline') or p.get('expires') or p.get('date')
            if status and (p.get('status')=='Closed' or (expires and expires<datetime.date.today().isoformat())):raise s.Bad('This listing is closed or expired')
            c.execute('DELETE FROM responses WHERE user_id=? AND entry_id=?',(uid,eid))
            if status:
                c.execute('INSERT INTO responses VALUES(?,?,?)',(uid,eid,status))
                if r['user_id']!=uid:notify(c,r['user_id'],me['first_name']+': '+status+' — '+p['name'],{'events':'events','groups':'studymate','clubs':'university','opportunities':'opportunities','discounts':'discounts'}[r['kind']]+'.html')
            return 200,{'ok':True}
    return None

def delete(path,me):
    m=re.fullmatch(r'/api/(entries|comments)/(\d+)',path)
    if not m:return None
    with s.connect() as c:
        cur=c.execute('DELETE FROM '+m[1]+' WHERE id=? AND user_id=?',(int(m[2]),me['id']))
    return (200,{'deleted':1}) if cur.rowcount else (404,{'error':'Record not found'})
