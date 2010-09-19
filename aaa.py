#!/usr/bin/env python
# coding: utf-8

import sys, re
import cgi
import codecs

import markdown

SUMMARY_LETTERS = u'ดรมฟซลท'

# Default content of newly creating score.
CONTENT_NEW = u"""\
:title:
:columns: 8
:category: ponglang
:style: ponglang
:body:

:desc:
:lyric:
"""

def _u(arg):
    """Unicode repr for debug."""
    if isinstance(arg, basestring):
        return u'"' + unicode(arg) + u'"'
    elif isinstance(arg, tuple):
        return u'(' + u', '.join([_u(i) for i in arg]) + u')'
    elif isinstance(arg, list):
        return u'[' + u', '.join([_u(i) for i in arg]) + u']'
    else:
        return unicode(arg)

class Cell():
    def __init__(self, src_line, src_col, src_len, src_text):
        # source data
        self.src_line = src_line
        self.src_col = src_col
        self.src_len = src_len
        self.src_text = src_text
        # produced data
        self.colspan = 1
        self.style_cls = None   # style class name of this cell.
        self.text_blocks = []

        # textblocks: [block,... ]
        # block: (blockdata, block_cls)
        # block_cls: 'class' or None
        # blockdata: 'text' or block

        # src text cell that begines with
        # ':'+N := cellspan N columns
        # ':'+space+text := text-class that fills whole line

        m = re.compile(r'^:(\d+)?\s*(.*)$').match(self.src_text)
        if m:
            if m.group(1):
                self.colspan = max(int(m.group(1)), 1)  # positive int
            else:
                # text-class. fill rest of cols.
                self.colspan = -1
                self.style_cls = u'text'
            self.src_text = self.src_text[m.start(2):]
            self.src_col += m.start(2)

    def __unicode__(self):
        return u'<Cell: src(line:%d col:%d len:%d text:"%s")\n'\
               u'       span:%d cls:"%s" text_blocks:%s>' % \
               (self.src_line, self.src_col, self.src_len, self.src_text,
                self.colspan, self.style_cls, _u(self.text_blocks))

class CellList(list):
    """Self is list of lines: [cells-of-line,...]
    cells-of-line: [cells,...]
    cells: [textblock,...]
    textblock: ('text', cls)
    """
    def __init__(self, maxcol=8):
        self.maxcol = maxcol
        self.cells = []
        self.ncol = 0
        self.emptycell = Cell(0, 0, 0, u'')
        self.cur_flags = []
        self.msgs = []
        self.cur_line = 0

    def flush(self, fill=True):
        """Add current cells to self.
        Set fill to True to fill current line by empty cells before add."""
        if not self.cells:
            return
        if fill:    # fill a line by empty cells
            if self.ncol < self.maxcol:
                self.cells += [self.emptycell] * (self.maxcol - self.ncol)
        # add buffered cells to self as a line.
        self.append(self.cells)
        self.cells = []
        self.ncol = 0

    def add_cell(self, cell):
        if cell.colspan < 0:
            # fill whole columns of a line.
            cell.colspan = self.maxcol
        if self.ncol + cell.colspan > self.maxcol:
            self.flush(fill=True)
            self.ncol = 0
        cell.text_blocks = self._cell_to_textblocks(cell)
        # buffering, not add to self yet.
        self.cells.append(cell)
        self.ncol += cell.colspan

    def add_end(self):
        self.flush(fill=True)
        if self.cur_flags:
            self.msgs.append(
                u'* Flags not closed at the end of text: %s' \
                % ','.join(self.cur_flags))

    def _cell_to_textblocks(self, cell):
        """Make textblock list for a cell.
        Enclose a cell by open/close frag as current flag status.
        Status of flags depends on previous cells.
        """
        self.cur_line = cell.src_line
        lst = self._tag_flags(self.cur_flags, isopen=True) + \
              self._text_to_textblocks(cell.src_text) + \
              self._tag_flags(self.cur_flags, isopen=False)
        return self._separate_flags(lst)

    def _tag_flags(self, flags, isopen):
        """Produce open or close tag for flags.
        return textblocks.
            flags: flag chars.
            isopen: True for open tag, False for close tag.
        """
        it = iter(flags) if isopen else reversed(flags)
        char = '}{'[int(isopen)] # set mark as open or close
        return [(c, char) for c in it]

    def _text_to_textblocks(self, src):
        """Make textblocks from src string.
        textblocks: list of textblock.
        textblock:
            "text" : textblock with no class
            (textblocks, 'C') : text that has class `C`. -- "text.C"
            ("marktext", 'mark') : text marked as class `mark`. -- "text^marktext"
            ("subtext", 'sub') : -- "text_subtext"
            ('C', '{') : begin class `C`
            ('C', '}') : end class `C`. -- "C<text>C"
        """
        if not src:
            return []

        # text.C : text has class `C` (1 char)
        m = re.compile(r'(\S+)\.(\w)\b').search(src)
        if m:
            c = m.group(2)
            lst = self._text_to_textblocks(src[:m.start()])
            tbs = self._text_to_textblocks(m.group(1))
            lst.append((tbs, c))
            lst.extend(self._text_to_textblocks(src[m.end():]))
            return lst

        # text^Mmm : text has mark Mmm, mark ends with space.
        m = re.compile(r'\^(\S+)\s?').search(src)
        if m:
            mark_text = m.group(1)
            lst = self._text_to_textblocks(src[:m.start()])
            lst.append((mark_text, 'mark'))
            lst.extend(self._text_to_textblocks(src[m.end():]))
            return lst

        # text_Mmm: text has subtext Mmm, ends with space.
        m = re.compile(r'_(\S+)\s?').search(src)
        if m:
            mark_text = m.group(1)
            lst = self._text_to_textblocks(src[:m.start()])
            lst.append((mark_text, 'sub'))
            lst.extend(self._text_to_textblocks(src[m.end():]))
            return lst

        # C<text...>C : texts has class `C`, may across columns and lines
        m = re.compile(r'(\w)<').search(src)
        if m:
            c = m.group(1)
            lst = self._text_to_textblocks(src[:m.start()])
            if c in self.cur_flags:
                self.msgs.append(
                    'Line %d: Duplicated flag: %s' %(self.cur_line, c))
                # embed source text and marked as error.
                lst.append((cgi.escape(m.group(0)), 'error'))
                lst.extend(self._text_to_textblocks(src[m.end():]))
                return lst

            self.cur_flags.append(c)
            lst.extend(self._tag_flags([c], isopen=True))
            lst.extend(self._text_to_textblocks(src[m.end():]))
            return lst

		# and closing class by "...>C"
        m = re.compile(r'>(\w)').search(src)
        if m:
            c = m.group(1)
            lst = self._text_to_textblocks(src[:m.start()])
            if c not in self.cur_flags:
                self.msgs.append(
                    'Line %d: Unmatched flag: %s' %(self.cur_line, c))
                # same as above.
                lst.append((cgi.escape(m.group(0)), 'error'))
                lst.extend(self._text_to_textblocks(src[m.end():]))
                return lst

            self.cur_flags.remove(c)
            lst.extend(self._tag_flags([c], isopen=False))
            lst.extend(self._text_to_textblocks(src[m.end():]))
            return lst

        # text without class
        return [src]

    def _separate_flags(self, lst):
        """Separate current flags for each cells.
        If the flag status accross cells,
        then add open and close tags for those cells."""
        for n, cell in enumerate(lst):
            if isinstance(cell, basestring):
                continue
            flagchar, cls = cell
            if cls == '{':
                try:
                    n2 = lst.index((flagchar, '}'), n + 1)
                except ValueError:
                    lst.append((flagchar, '}'))
                    n2 = len(lst) - 1
                child = self._separate_flags(lst[n + 1:n2])
                if child:
                    middle = [(child, flagchar)]
                else:
                    middle = []
                return self._separate_flags(lst[:n]) \
                       + middle \
                       + self._separate_flags(lst[n2 + 1:])
        for n, cell in enumerate(lst):
            if isinstance(cell, basestring):
                continue
            flagchar, cls = cell
            if cls == '}':
                child = self._separate_flags(lst[:n])
                if child:
                    front = [(child, flagchar)]
                else:
                    front = []
                return front + self._separate_flags(lst[n + 1:])
        return lst

    def dump(self):
        r = []
        for line in self:
            for col in line:
                r.append(unicode(col))
        return u'\n'.join(r)

    def __str__(self):
        l = []
        for line in self:
            l.append(','.join([str(cell) for cell in line]))
        return '\n'.join(l)

    def html_table(self, table_cls=u'ponglang'):
        """Make a html table from self."""

        tcs = [u'scorethai']
        if table_cls:
            tcs.append(table_cls)
        trs = []
        for line in self:
            tds = []
            for col in line:
                td = ['<td']
                if col.colspan > 1:
                    td.append('colspan="%d"' % col.colspan)
                if col.style_cls:
                    td.append('class="%s"' % cgi.escape(col.style_cls))
                tds.append(
                    u'%s>%s</td>' % \
                    (' '.join(td), self.html_cell(col.text_blocks))
                )
            trs.append(' <tr>%s</tr>' % ''.join(tds))
        return u'<table class="%s">\n%s\n</table>\n' \
                %(' '.join(tcs), '\n'.join(trs))

    def html_cell(self, cellblock):
        """Make a html cell for cellblock.
        cellblock may text, tuple of (text, class), or list of cellblock.
        """
        if isinstance(cellblock, basestring):
            return cellblock
        if isinstance(cellblock, list):
            return ''.join([self.html_cell(i) for i in cellblock])
        s, cls = cellblock
        if not cls:
            return cgi.escape(s)
        return '<span class="%s">' % cgi.escape(cls) + self.html_cell(s) + '</span>'

    def _tb_to_summary(self, tb):
        # print ' tb: %s' % _u(tb) #dbg
        if isinstance(tb, basestring):
            return ''.join(i for i in tb if i in SUMMARY_LETTERS)
        if isinstance(tb, list):
            return ''.join([self._tb_to_summary(i) for i in tb])
        if tb[1] == 'mark':
            return ''
        return self._tb_to_summary(tb[0])

    def make_summary(self):
        r = u''
        lastchar = None
        for line in self:
            for col in line:
                # print 'col: %s' % unicode(col) #dbg
                if col.style_cls == 'text':
                    continue
                for c in self._tb_to_summary(col.text_blocks):
                    if c != lastchar:
                        r += c
                        lastchar = c
        return r

class Body():
    def __init__(self, maxcol=8):
        self.linenum = 0
        self.cells = CellList(maxcol=maxcol)

    def _split_cell(self, src):
        reCell = re.compile(r'([^,]*)(,?\s*)')
        for m in reCell.finditer(src):
            if m.group(1) or m.group(2):
                # (src_col, src_len, text)
                yield((m.start(1), m.end(1) - m.start(1), m.group(1)))

    def readtext(self, src, linenum=0):
        self.linenum = linenum  # start line number of source text.
        for i in src.splitlines():
            self.linenum += 1
            s = i.strip()

            # src text line that ends with ',/' or '/,' means force-newline
            m = re.compile(r'^(.*)\s*(,?\s*/|/\s*,?)$').match(s)
            if m:
                s = m.group(1)
                newline = True
            else:
                newline = False
            if s.endswith(','):
                s = s[:-1]
            if s:
                for i in self._split_cell(s):
                    self.cells.add_cell(Cell(self.linenum, i[0], i[1], i[2]))
            if newline:
                self.cells.flush(fill=True)
        self.cells.add_end()

    def get_msgs(self):
        return self.cells.msgs

    def __repr__(self):
        return repr(self.cells)

    def html_table(self, table_cls):
        return self.cells.html_table(table_cls)

    def count_lines(self):
        return len(self.cells)

class Src():
    def __init__(self, linenum, label, text):
        self.linenum = linenum
        self.label = label
        self.text = text

    def __str__(self):
        return u'%d:%s: "%s"' % (self.linenum, self.label, self.text)

class Parser():
    """Scorethai source text reader.
        source text format:
            :label1: text1
            :label2: text2
            :label3:
            text3-1
            text3-2
            ...
    """
    def __init__(self):
        self.linenum = 0    # src line#
        self.srcs = []      # [Src(),...]
        self.messages = []  # [message-text,...]
        self.summary = u''  # summary text of song body
        self.html_body = u''# html body text, produced by parse()
        self.body_lines = 0  # #of produced lines, to check empty body

    def readtext(self, src, linenum=0):
        #if src and src[0] == u'\ufeff':
        #    # print 'skip:%r' % src[0] #dbg
        #    src = src[1:]
        self.linenum = linenum
        label = text = None
        for i in src.splitlines():
            self.linenum += 1
            # don't strip leading space. use for markdown text.
            s = i.rstrip()
            # :label: text
            m = re.compile(r'^:([^:\s]+):\s*(.*)$').match(s)
            if m:
                label, text = m.groups()
            else:
                text = s
            if label:
                self.srcs.append(Src(self.linenum, label, text))
            else:
                self.messages.append(
                    'No label defined for line %d: "%s"' \
                        % (self.linenum, text))

    def get_title_one_line(self):
        return ' / '.join([i.text for i in self.srcs if i.label == u'title'])

    def enum_label_text(self):
        s = None
        for i in self.srcs:
            if s:
                if s.label == i.label:
                    if s.text:
                        s.text += u'\n'
                    s.text += i.text
                    continue
                else:
                    yield s
            s = Src(i.linenum, i.label, i.text)
        if s:
            yield s

    def getlabels(self):
        last = None
        for i in self.srcs:
            if i.label != last:
                yield i.label
                last = i.label

    def gettext(self, label):
        r = u''
        for i in self.srcs:
            if i.label == label:
                if r:
                    r += u'\n'
                r += i.text
        return r

    def __repr__(self):
        return repr(self.srcs)

    def __str__(self):
        return '\n'.join([str(i) for i in self.srcs])

    def gettext_splitted(self, label):
        return self.gettext(label).splitlines()

    """
    def html_title_in_body(self):
        a = self.gettext_splitted(u'title')
        if a:
            s = u'<br />'.join([cgi.escape(i) for i in a])
        else:
            s = u'No Title'
        return u'<h1 class="title">' + s + u'</h1>\n'
    """

    def get_html_message(self):
        s = '\n'.join(self.messages)
        if s:
            return '<pre class="error">\n' + cgi.escape(s) + '\n</pre>\n'
        else:
            return u''

    def parse(self, cols_override=0, dump=False):
        """Use `cols_override` to override columns specified in the score.
        """
        table_cls = 'ponglang'  # default table style
        self.columns = cols_override or 8 # default max-columns
        self.categories = []    # reset category list
        r = u''
        for i in self.enum_label_text():
            if dump:
                print u'%d:%s:"%s"' % (i.linenum, i.label, i.text)
            if i.label == u'title':
                r += u'<h1 class="title">' + \
                     u'<br />'.join(
                        [cgi.escape(s) for s in i.text.splitlines()]) + \
                     u'</h1>\n'
            elif i.label == u'category':
                for c in re.split(r'[\s,]+', i.text):
                    self.categories.append(c)
            elif i.label == u'style':
                table_cls = i.text
            elif i.label == u'columns':
                if cols_override == 0:
                    if i.text.isdigit():
                        self.columns = int(i.text)
                    else:
                        self.messages.append(
                            'Line %d: columns must be digits: %s' %\
                            (i.linenum, i.text))
            elif i.label == u'body':
                body = Body(maxcol=self.columns)
                body.readtext(i.text, i.linenum - 1)
                ss = body.get_msgs()
                if ss:
                    self.messages.extend(ss)
                r += body.html_table(table_cls)
                self.summary += body.cells.make_summary()
                self.body_lines += body.count_lines()
                if dump:
                    print u'dump:\n%s' % body.cells.dump()
                    print u'summary: "%s"' % self.summary
            elif i.label == u'lyric':
                if i.text:
                    r += u'<div class="%s">%s</div>' \
                            %(i.label, cgi.escape(i.text))
            elif i.label == u'desc':
                if i.text:
                    # r += u'text:<div>%s</div>' % repr(i.text) #dbg
                    h = markdown.markdown(
                        i.text,
                        output_format='xhtml1',
                        )#safe_mode="escape")
                    r += u'<div class="%s">%s</div>' %(i.label, h)
            else:
                self.messages.append(
                    'Line %d: Unknown label:%s: %s' %\
                    (i.linenum, i.label, i.text))

        if not self.body_lines:
            self.messages.append('* content :body: is empty.')

        self.html_body += self.get_html_message()
        self.html_body += r

    def _test(self, src):
        self.readtext(src)
        self.parse(dump=True)
        html = u"""\
<html>
<link type="text/css" rel="stylesheet" href="stylesheets/scorethai.css" />
<title>""" + self.get_title_one_line() + """</title>
<body>
""" + self.html_body + """
</body></html>
"""
        codecs.open(u'1.html','wb', encoding='utf_8').write(html)

def _test():
    #try:
    #    import locale
    #    locale.setlocale(locale.LC_ALL, '')
    #except:
    #    pass

    #s = codecs.open('LaiPongLang.txt', encoding='utf_8_sig').read()
    s = sys.stdin.read()
    Parser()._test(s)

class ContentReader():
    def __init__(self, content, cols_override=0):
        """cols_override: override columns specified in the score."""
        self.parser = Parser()
        self.parser.readtext(content)
        self.parser.parse(cols_override=cols_override)

    def result(self):
        return (self.parser,
                self.parser.get_title_one_line(),
                self.parser.categories,
                self.parser.summary[:500])

    def html(self):
        return self.parser.html_body

    def info(self):
        return 'title: "%s"\n' \
               'categories: %s\n' \
               'summary: "%s"\n' \
               %(
                self.parser.get_title_one_line(),
                ', '.join(self.parser.categories),
                self.parser.summary,
               )

    def messages(self):
        return self.parser.messages

    def make_html(self):
        from string import Template
        from os import path
        import urllib
        temp = Template('''\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
   "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
 <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />
 <meta http-equiv="Content-Script-Type" content="text/javascript" />
 <link type="text/css" rel="stylesheet" href="$cssdir/scorethai.css" />
 <title>scorethai: $title</title>
</head>
<body>
$body
</body>
''')
        cssdir = path.join(path.dirname(sys.argv[0]), 'stylesheets')
        return temp.substitute(
            cssdir=urllib.pathname2url(cssdir),
            title=self.parser.get_title_one_line(),
            body=self.parser.html_body)

def make_html_file(filename, content):
    c = ContentReader(content)
    codecs.open(filename, 'wb', encoding='utf_8').write(c.make_html())
    sys.stderr.write('input: %s\n' % filename + c.info())
    msgs = c.messages()
    if msgs:
        sys.stderr.write(
            '\n '.join(
                [i for i in ['Messages:'] + msgs]))

if __name__=="__main__":
    sys.stdin = codecs.getreader('utf_8_sig')(sys.stdin)
    sys.stdout = codecs.getwriter('utf_8')(sys.stdout)
    sys.stderr = codecs.getwriter('utf_8')(sys.stderr)

    print sys.argv[0]
    if len(sys.argv) > 1:
        for i in sys.argv[1:]:
            s = codecs.open(i, 'r', encoding='utf_8_sig').read()
            make_html_file(i+'.html', s)
    else:
        make_html_file('a.html', sys.stdin.read())
