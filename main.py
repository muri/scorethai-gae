#!/usr/bin/env python
# coding: utf-8

import sys, os, re, cgi, logging
from StringIO import StringIO
from datetime import datetime
import wsgiref.handlers
from google.appengine.api import users
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import template
from django.utils import feedgenerator
from google.appengine.api import memcache

logging.getLogger().setLevel(logging.DEBUG)

APP_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(APP_DIR, 'markdown.zip'))
sys.path.insert(0, APP_DIR)
TMPL_DIR = os.path.join(APP_DIR, 'templates')

# logging.info("sys.path=%r" % sys.path)

#import scorethai # causes import error (unknown reason).
import aaa
scorethai = aaa


class ErrorScoreNotFound(Exception):
    pass
class ErrorScoreDataNotFound(Exception):
    pass

class Score(db.Model):
    creater = db.UserProperty(required=True)
    created = db.DateTimeProperty(auto_now_add=True)
    lastmodifier = db.UserProperty()
    lastmodified = db.DateTimeProperty(auto_now=True)
    title = db.StringProperty(multiline=True)
    summary = db.StringProperty(multiline=False)
    categories = db.StringListProperty()
    deleted = db.BooleanProperty(default=False)

    @property
    def numdata(self):
        query = ScoreData.all().ancestor(self)
        return query.count()

    def get_data(self, dkey):
        return ScoreData.get_by_key_name(dkey, parent=self)

    def get_all_data(self):
        query = ScoreData.all().ancestor(self).order('-date')
        return query.fetch(query.count())

    def get_last_content(self):
        query = ScoreData.all().ancestor(self).order('-date')
        data = query.get()
        if not data:
            raise ErrorScoreDataNotFound(
                'ScoreData not found for score: %s' % self.key().name())
        return data.content

    def purge_with_data(self):
        deleted = 0
        while True:
            query = ScoreData.all().ancestor(self)
            if query.count() < 1:
                break
            for data in query.fetch(100):
                data.delete()
                deleted += 1
        self.delete()
        return deleted

    def get_permission_for(self, u):
        # u is instance of UserInfo
        isowner = u.user == self.creater
        deleter = u.isoperator or isowner or u.can_delete_other
        return {
            'listable': not self.deleted or deleter,
            'editable': isowner or u.isoperator or u.can_modify_other,
            'deleteable': not self.deleted and deleter,
            'undeleteable': self.deleted and deleter,
            'purgeable': self.deleted and users.is_current_user_admin(),
        }

    @classmethod
    def get_score(cls, skey):
        if not skey:
            raise ErrorScoreNotFound('Invalid score keyname.')
        try:
            score = Score.get_by_key_name(skey)
        except db.BadKeyError, e:
            raise ErrorScoreNotFound(str(e))
        if not score:
            raise ErrorScoreNotFound('Score not found: %s' % skey)
        return score

class ScoreData(db.Model):
    title = db.StringProperty(required=True, multiline=True)
    content = db.TextProperty(required=True)
    summary = db.StringProperty()
    user = db.UserProperty(required=True)
    date = db.DateTimeProperty(auto_now_add=True)
    version = db.IntegerProperty(required=True)

class UserInfo(db.Model):
    user = db.UserProperty(required=True)
    isoperator = db.BooleanProperty(default=False)
    can_addnew = db.BooleanProperty(default=True)
    can_modify_other = db.BooleanProperty(default=False)
    can_delete_other = db.BooleanProperty(default=False)
    lastlogin = db.DateTimeProperty(auto_now=True)

    def update(self):
        if users.is_current_user_admin() and not self.isoperator:
            # set operator flag, only when admin's user info is not set.
            self.isoperator = True
        return self.put()

    def logined(self):
        return self.user.nickname() != 'anonymous'

    @classmethod
    def get_userinfo(cls):
        user = users.get_current_user()
        if user:
            anon = False
        else:
            user = users.User('anonymous')  # as anonymous user
            anon = True
        query = cls.all().filter('user =', user)
        ui = query.get()
        if not ui:
            ui = cls(user=user)
            if anon:
                ui.can_addnew = False
            ui.put()
            Log.add('info', u'new user: %s' % user)
        return ui

class Log(db.Model):
    info = db.CategoryProperty(required=True)
    date = db.DateTimeProperty(auto_now_add=True)
    title = db.StringProperty(required=True)
    description = db.TextProperty()
    link = db.StringProperty()
    author_name = db.StringProperty()
    categories = db.StringListProperty()

    @classmethod
    def add(cls, info, title,
        description='', author_name='', link='', categories=[]):
        log = Log(info=info, title=cgi.escape(title),
                  description=description, author_name=author_name,
                  link=link, categories=categories)
        log.put()

def common_temp(self):
    """Make template values."""
    userinfo = UserInfo.get_userinfo()
    if userinfo.logined():
        loginout_url = users.create_logout_url(self.request.path)
        isadmin = users.is_current_user_admin()
    else:
        loginout_url = users.create_login_url(self.request.path)
        isadmin = False

    values = {
        'req_path' : self.request.path,
        'offer_mobile_link' : self.offer_mobile_link,
        'uri': self.request.uri,
        'header': self.header,
        'searchbox' : self.searchbox,
        'loginout_url' : loginout_url,
        'user' : userinfo.user,
        'logined' : userinfo.logined(),
        'isoperator' : userinfo.isoperator,
        'isadmin' : isadmin
    }
    if self.request.get('dump'):
        values['dump'] = True
    return values

def common_page_error(self, msgs):
    """Common error page."""
    if isinstance(msgs, basestring):
        msgs = [msgs]
    values = self.temp()
    values['msgs'] = msgs
    values['uri'] = self.request.uri
    values['headers'] = cgi.escape(
        '\n'.join([
            u'%s: %s' % (k, v)
                for k, v in self.request.headers.iteritems()
        ])
    )
    values['arguments'] = cgi.escape(
        '\n'.join([
            u'%s = "%s"' % (i, self.request.get(i))
                for i in self.request.arguments()
        ])
    )
    path = os.path.join(TMPL_DIR, 'error.html')
    self.response.set_status(500)
    self.response.out.write(template.render(path, values))

class MainPage(webapp.RequestHandler):
    """Main page handler."""
    temp = common_temp
    page_error = common_page_error

    # IE6 cannot use multi submit buttons with same name?
    # replace operation as each reqest keys.
    OP_SUBS = (
        ('preview', 'view'),
        ('save', 'save'),
        ('delete', 'delete'),
        ('undelete', 'undelete'),
    )

    def post(self):
        # request parameters that often use.
        op = self.request.get('op')             # operation
        skey = self.request.get('key')          # score keyname
        dkey = self.request.get('datakey')      # scoredata keyname
        content = self.request.get('content')   # content
        dump = self.request.get('dump')         # debug

        self.req_path = self.request.path
        #logging.info('path:%r' % self.req_path)

        self.mobile = self.offer_mobile_link = ''
        if self.req_path.endswith('/m/') or self.req_path.endswith('/m'):
            self.mobile = True
            logging.info('for mobile')
        else:
            if 'mobile' in self.request.user_agent.lower():
                self.offer_mobile_link = '/m/'

        self.acc = self.request.get('acc')      # accesser
        if self.req_path.endswith('/tab/') or self.req_path.endswith('/tab'):
            self.acc = 'tab'    # facebook tab

        self.header = (self.acc != 'tab')       # from facebook tab?
        self.searchbox= True    # show search box by default

        # replace operation.
        for t in self.OP_SUBS:
            if self.request.get(t[0]):
                op = t[1]
                break

        userinfo = UserInfo.get_userinfo()
        userinfo.update()   # update last-login time

        msgs = []
        if dump:
            for i in self.request.arguments():
                msgs.append('%s="%s"' % (str(i), self.request.get(i)))
            logging.debug('\n'.join(msgs))

        try:
            if not op or op == 'list':
                self.page_list(userinfo, msgs)
            elif op == 'view':
                self.page_view(skey, content)
            elif op == 'edit':
                self.op_edit(userinfo, msgs, skey, dkey, content)
            elif op == 'save':
                self.op_save(userinfo, msgs, skey, dkey, content)
            elif op == 'check':
                self.op_check(userinfo, skey, dkey, msgs)
            elif op == 'delete':
                self.op_delete(userinfo, msgs, skey)
            elif op == 'undelete':
                self.op_undelete(userinfo, msgs, skey)
            elif op == 'purge':
                self.op_purge(userinfo, msgs, skey)
            elif op == 'changes':
                self.page_changes(userinfo, msgs, skey)
            elif op == 'rawfile':
                self.op_rawfile(skey)
            elif op == 'rawfiles':
                self.op_rawfiles()
            else:
                self.page_error(u'Unknown operation: %s' % op)
        except Exception, e:
            self.page_error(str(e))

    get = post

    # operations.

    def op_edit(self, userinfo, msgs, skey, dkey, content):
        if skey == 'new':
            # edit a new score with the default new content.
            msgs.append(u'Creating new score.')
            if not userinfo.logined():
                msgs.append(u'You are currently not login. \
                    Score would not be saved (can preview only).')
            content = scorethai.CONTENT_NEW
            score = None
        else:
            # edit the existing score.
            score, content = self.load_score_content(skey, dkey, msgs)
            if not score or not content:
                return
            msgs.append(u'Editting score. key=%s' % str(score.key().name()))
        self.page_edit(userinfo, score, content, msgs)

    def op_check(self, userinfo, skey, dkey, msgs):
        score, content = self.load_score_content(skey, dkey, msgs)
        if not score or not content:
            return
        if not userinfo.logined():
            msgs.append(u'Need login.')
            self.page_error(msgs)
            return

        modifies = []
        parser, title, cats, summary = \
                scorethai.ContentReader(content).result()
        msgs.extend([
            u'checking:',
            u' skey=%s' % skey,
            u' title=%s' % score.title,
            u' categories=%s' % ','.join(score.categories),
            u' summary=%s' % score.summary,
        ])
        if title != score.title:
            score.title = title
            modifies.append(u'title=%s' % title)
        if cats != score.categories:
            score.categoies = cats
            modifies.append('categories=%s', ','.join(cats))
        if summary != score.summary:
            score.summary = summary
            modifies.append('summary=%s' % summary)
        if modifies:
            msgs.extend([u'modified:'] + modifies)
            score.put()
        else:
            msgs.append(u'No change.')
        self.page_list(userinfo, msgs)

    def op_save(self, userinfo, msgs, skey, dkey, content):
        if not content:
            self.page_error('No content.')
            return

        parser, title, cats, summary = \
                scorethai.ContentReader(content).result()

        if not userinfo.logined():
            msgs.append(u'Need login to save.')
        if parser.messages:
            # parser may report errors.
            msgs.append(u'Error in contents.')
            msgs.extend(parser.messages)
        if not title:
            msgs.append(u'No title in content. Use ":title: ..." tag.')

        if skey == 'new':
            score = None
        else:
            # update existing score.
            score = Score.get_score(skey)
        if msgs:
            # report any errors and go to edit.
            msgs.insert(0, u'Error on save.')
            self.page_edit(userinfo, score, content, msgs)
            return

        if score:
            # updating an existing score.
            perm = score.get_permission_for(userinfo)
            if not perm['editable']:
                msgs.append(u'You have no permission to modify this score.')
                self.page_edit(userinfo, score, content, msgs)
                return

            if content == score.get_last_content():
                msgs.append(u'Cannot save. Content has not been changed.')
                self.page_edit(userinfo, score, content, msgs)
                return

            version = score.numdata + 1
            msg_saved = u'Modified score: '
            feed_title = u'%s modified score: %s'
        else:
            # creating new score.
            score = Score(
                key_name=self.make_keyname('s-', userinfo.user.nickname()),
                creater=userinfo.user)
            version = 1
            msg_saved = u'Saved score: '
            feed_title = u'%s added score: %s'

        # create and save score-data.
        data = ScoreData(parent=score,
                key_name=self.make_keyname('d-', userinfo.user.nickname()),
                title=title,
                content=content, summary=summary,
                user=userinfo.user, version=version)
        data.put()

        # save score.
        score.lastmodifier = userinfo.user
        score.title = title
        score.content = content
        score.summary = summary
        score.categories = cats
        score.put()
        key_name = score.key().name()

        scats = ','.join(cats)
        msg_saved += u'%s, category:[%s] version:%d' \
                     % (key_name, scats, version)
        msgs.append(msg_saved)

        nick = userinfo.user.nickname()
        link = u'http://%s/?op=view&amp;key=%s' \
                % (self.request.host, key_name)
        Log.add('score',
                feed_title % (nick, title),
                description=link,#parser.html_body,
                author_name=nick,
                link=link,
                categories=cats)

        self.page_edit(userinfo, score, content, msgs)

    def op_delete(self, userinfo, msgs, skey):
        # delete means mark-as-delete, or hide.
        # the owner still can view, edit, or undelete it.
        score = Score.get_score(skey)
        perm = score.get_permission_for(userinfo)
        if not perm['deleteable']:
            msgs.append(u'No permission to delete score: %s' % skey)
        else:
            score.deleted = True
            score.put()
            msgs.append(u'Marked as deleted: %s' % skey)
            Log.add('info',
                    u'%s deleted %s' % (userinfo.user, score.title))
        self.page_list(userinfo, msgs)

    def op_undelete(self, userinfo, msgs, skey):
        # means unmark-as-delete, or show.
        score = Score.get_score(skey)
        perm = score.get_permission_for(userinfo)
        if not perm['undeleteable']:
            msgs.append(u'No permission to undelete: %s' % skey)
        else:
            score.deleted = False
            score.put()
            msgs.append(u'Unmarked as deleted: %s' % skey)
            Log.add('info',
                    u'%s undeleted %s' % (userinfo.user, score.title))
        self.page_list(userinfo, msgs)

    def op_purge(self, userinfo, msgs, skey):
        # purge means really delete from datastore.
        score = Score.get_score(skey)
        perm = score.get_permission_for(userinfo)
        if not perm['purgeable']:
            msgs.append(u'No permission to purge.: key=%s' % skey)
        else:
            n = score.purge_with_data()
            s = u'score: %s with %d data.' % (skey, n)
            msgs.append(u'Purged ' + s)
            Log.add('info', u'%s purged %s' % (userinfo.user, s))
        self.page_list(userinfo, msgs)

    # misc.

    def make_keyname(self, prefix, nick):
        return prefix + ''.join([i for i in nick if i.isalpha()]) + '-' + \
            datetime.today().strftime('%y%m%d%H%M%S')

    def load_score_content(self, skey, dkey, msgs):
        "Load existing score."
        score = Score.get_score(skey)
        if dkey:
            # from history data. target is a scoredata of the score.
            data = score.get_data(dkey)
            if not data:
                msgs.append(
                    u'data: %s is not found in score: %s' %(dkey, skey))
                self.page_changes(userinfo, msgs, skey)
                return (score, None)
            content = data.content
        else:
            # data-key was not specified,
            # load the last content of the score.
            content = score.get_last_content()
        return (score, content)

    def insert_tag_match(self, pattern, src):
        # find src string by regex pattern,
        # return tuple: (matched-count, result-string)
        match = 0
        result = u''
        while src:
            m = re.search(pattern, src, re.I)
            if m:
                result += src[:m.start()] + \
                        '<span class="found">%s</span>' \
                            % cgi.escape(src[m.start():m.end()])
                src = cgi.escape(src[m.end():])
                match += 1
            else:
                result += cgi.escape(src)
                break
        return (match, result)

    # output pages.

    def page_list(self, userinfo, msgs):
        # search options
        title = self.request.get('title')
        if title:
            msgs.append(u'search by title: %s' % title)
        summary = self.request.get('summary')
        if summary:
            summary = scorethai.regularize_summary_letters(summary)
            msgs.append(u'search by summary: %s' % summary)

        cat = self.request.get('cat', u'').lower() # category
        if cat:
            msgs.append(u'in category: %s' % cat)

        order = self.request.get('order')
        if order and order in (
                'title', '-title', '-lastmodified', 'lastmodified'):
            pass
        elif title or summary or cat:
            order = 'title'
        else:
            order = '-lastmodified'

        query = Score.all()
        if cat:
            query.filter('categories =', cat)
        query.order(order)
        ndata = query.count()
        scores = query.fetch(ndata)

        limit = self.request.get_range('limit', 1, 100, 10)
        maxpage = (ndata + limit - 1) // limit
        page = self.request.get_range('page', 1, maxpage, 1)
        offset = (page - 1) * limit

        # django template cannot test condition
        #  as {% if isdamin and creater == user %} (?),
        # and cannot use variable begins with '_',
        # so copy all of them to another dict-list for passing flags.
        dics = []
        ndata = 0
        end_data = offset + limit
        for i in scores:
            dic = i.get_permission_for(userinfo)
            if not dic['listable']:
                continue

            # datastore doesn't support middle-matching query?
            if title:
                match, display_title = self.insert_tag_match(title, i.title)
                if not match:
                    continue
            else:
                display_title = None

            if summary:
                match, display_summary = \
                        self.insert_tag_match(summary, i.summary)
                if not match:
                    continue
            else:
                display_summary = None

            ndata += 1
            if ndata <= offset or ndata > end_data:
                continue

            dic.update({
                'num': ndata,
                'key': i.key().name(),
                'id': i.key().id(),
                'title': i.title,
                'display_title': display_title,
                'categories': [c for c in i.categories],
                'deleted': i.deleted,
                'summary': i.summary,
                'display_summary': display_summary,
                'creater': i.creater,
                'created': i.created,
                'lastmodifier': i.lastmodifier,
                'lastmodified': i.lastmodified,
                'version': i.numdata,
            })
            dics.append(dic)


        # args for next page navi
        args = (
            ('order', order),
            ('limit', limit),
            ('title', title),
            ('summary', summary),
            ('cat', cat),
        )
        pageargs = '&amp;'.join([
            '%s=%s' % (name, val) for name, val in args if val
        ])

        # use template
        values = self.temp()
        values['scores'] = dics
        values['msgs'] = [cgi.escape(i) for i in msgs]
        values['pageargs'] = pageargs + '&amp;page=%d' % page

        # page navi
        if ndata:
            maxpage = (ndata + limit - 1) // limit
            ss = []
            for i in range(1, maxpage + 1):
                s = str(i)
                if i != page:
                    s = '<a href="/?%s&amp;page=%s">%s</a>' % (pageargs, s, s)
                ss.append(s)
            pagenavi = 'page [%s]' % ' '.join(ss)
            if dics:
                pagenavi += ' score %d to %d of %d' \
                            % (offset + 1, offset + len(dics), ndata)
        else:
            pagenavi = 'No data found.'
        values['pagenavi'] = pagenavi

        # restore form
        values['title'] = title or ''
        values['summary'] = summary or ''
        values['cat'] = cat
        values['limit'] = limit
        values['order'] = order
        path = os.path.join(TMPL_DIR, 'main.html')
        self.response.out.write(template.render(path, values))

    def page_view(self, skey, content):
        """if has `content` then view it,
            else load score by `skey`."""
        if not content:
            content = Score.get_score(skey).get_last_content()

        # if cols is not specified and viewer is mobile,
        # then force cols to 4
        cols = self.request.get_range('cols', 0, 16, 0)
        if cols == 0 and self.mobile:
            cols = 4
            logging.info('page_view: force cols=%d' % cols)

        parser = scorethai.ContentReader(content, cols_override=cols).parser
        self.header = False
        values = self.temp()
        values['title'] = parser.get_title_one_line()
        values['body'] = parser.html_body
        path = os.path.join(TMPL_DIR, 'view.html')
        self.response.out.write(template.render(path, values))

    def page_edit(self, userinfo, score, content, msgs):
        self.searchbox = False
        self.header = False
        values = self.temp()
        if score:
            skey = score.key().name()
            values.update(score.get_permission_for(userinfo))
        else:
            skey = u'new'
        values['key'] = skey
        values['msgs'] = [cgi.escape(i) for i in msgs]
        values['content'] = cgi.escape(content)
        path = os.path.join(TMPL_DIR, 'edit.html')
        self.response.out.write(template.render(path, values))

    def page_changes(self, userinfo, msgs, skey):
        score = Score.get_score(skey)
        dics = []
        query = ScoreData.all().ancestor(score).order('-date')
        for data in query.fetch(100):
            dics.append({
                'key': data.key().name(),
                'id': data.key().id(),
                'version' : data.version,
                'date': data.date,
                'user' : data.user,
                'content' : data.content,
            })

        values = self.temp()
        values['datas'] = dics
        values['key'] = skey
        values['msgs'] = [cgi.escape(i) for i in msgs]
        perm = score.get_permission_for(userinfo)
        values['editable'] = perm['editable']
        path = os.path.join(TMPL_DIR, 'changes.html')
        self.response.out.write(template.render(path, values))

    # raw file operaitons.

    def op_rawfile(self, skey):
        # retrieve raw content
        score = Score.get_score(skey)
        content = score.get_last_content()
        # send content as attachment file.
        self.response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        self.response.headers.add_header('content-disposition',
            'attachment', filename=skey.encode('utf-8'))
        self.response.out.write(content)

    def op_rawfiles(self):
        # http://code.google.com/intl/ja/appengine/docs/datastore/queriesandindexes.html
        query = Score.gql('ORDER BY __key__')

        # Use a query parameter to keep track of the last key of the last
        # batch, to know where to start the next batch.
        last_key_str = self.request.get('last')
        if last_key_str:
            last_key = db.Key(last_key_str)
            query = Score.gql('WHERE __key__ > :1 ORDER BY __key__', last_key)

        # For batches of 20, fetch 21, then use result #20 as the "last"
        # if there is a 21st.
        entities = query.fetch(21)
        new_last_key_str = None
        if len(entities) == 21:
            new_last_key_str = str(entities[19].key())

        # Return the data and new_last_key_str.  Client would use
        # http://...?last=new_last_key_str to fetch the next batch.
        self.header = False
        values = self.temp()
        values['keys'] = [i.key().name() for i in entities]
        values['last'] = new_last_key_str
        path = os.path.join(TMPL_DIR, 'rawfiles.html')
        self.response.out.write(template.render(path, values))

class FeedPage(webapp.RequestHandler):
    def get(self):
        self.response.headers["Content-Type"] = "text/xml; charset=utf-8"
        self.response.out.write(self.get_output())
        #stats = memcache.get_stats()
        #logging.debug('Cache(FeedPage) Hits:%s Misses:%s' \
        #              % (stats['hits'], stats['misses']))

    def get_output(self):
        key = str(hash(self.request.uri))
        s = memcache.get(key)
        if s is not None:
            return s
        s = self.render()
        if not memcache.add(key, s, 60):
            logging.error("Memcache set failed.")
        return s

    def render(self):
        base_url = u'http://' + self.request.host + u'/'
        info = self.request.get('info')

        feed = feedgenerator.Rss201rev2Feed(
            title=u"Scorethai",
            link=base_url,
            description=u"Thai-isan music score database.",
            language=u"en")

        query = Log.all()
        if not info:
            info = 'score'
        if info != 'all':
            query.filter('info =', info)
        query.order('-date')
        for i in query.fetch(20):
            feed.add_item(
                title=i.title,
                link=i.link or base_url,
                description=i.description,
                author_name=i.author_name,
                categories=i.categories,
                pubdate=i.date,
            )

        return feed.writeString('utf-8')

class OperatorPage(webapp.RequestHandler):
    page_error = common_page_error
    temp = common_temp

    def post(self):
        userinfo = UserInfo.get_userinfo()
        if not userinfo.isoperator:
            self.page_error(u'Not the operator.')
            return

        msgs = []

        # specify target user to modify userinfo.
        target_user = None
        ukey = self.request.get('ukey')
        if ukey:
            try:
                target_user = db.get(db.Key(ukey))
            except:
                self.page_error('UserInfo not found. key:%s' % ukey)
                return

        # toggle boolean value of the target-userinfo.
        toggle = self.request.get('toggle')
        if toggle and target_user:
            if not self.check_admin():
                return

            if not hasattr(target_user, toggle):
                msgs.append(
                    'no attribute:%s for user: %s' %(
                    toggle, target_user.user))
            elif not isinstance(getattr(target_user, toggle), bool):
                msgs.append(
                    'attribute:%s is not bool type. type=%s' %(
                    toggle, type(getattr(target_user, toggle))))
            else:
                setattr(target_user, toggle, not getattr(target_user, toggle))
                target_user.put()
                msgs.append(
                    'Changed value:%s, for user:%s' %(
                    toggle, target_user.user))

        # operation "purge-orphan"
        op = self.request.get('op')
        if op == 'purgeorphan':
            if not self.check_admin():
                return

            count = 0
            for data in ScoreData.all():
                if not data.parent():
                    data.delete()
                    count += 1
            msgs.append('Purged %d data.' % count)

        self.header = False
        self.searchbox = False
        self.page_main(msgs=msgs)

    get = post

    def page_main(self, msgs=[]):
        values = self.temp()
        query = UserInfo.all().order('-lastlogin')
        values['userinfos'] = query.fetch(100)

        datas = []

        op_list = self.request.get('list')
        if op_list == 'orphan':
            msgs.append('list orphan data.')
            values['list_header'] = 'orphans.'
            query = ScoreData.all()
            for data in ScoreData.all():
                if not data.parent():
                    datas.append(data)

        values['datas'] = datas
        values['msgs'] = msgs
        path = os.path.join(TMPL_DIR, 'operator.html')
        self.response.out.write(template.render(path, values))

    def check_admin(self):
        if users.is_current_user_admin():
            return True
        self.page_error(u'Not the admin.')
        return False

def main():
    app = webapp.WSGIApplication([
        (r'/_o', OperatorPage),
        (r'/feed', FeedPage),
        (r'/', MainPage),
        (r'/m/?', MainPage),
        (r'/tab/?', MainPage),
    ], debug=True)
    wsgiref.handlers.CGIHandler().run(app)

if __name__ == "__main__":
  main()
