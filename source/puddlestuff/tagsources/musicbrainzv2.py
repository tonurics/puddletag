import pdb, sys, time, urllib, urllib2

from collections import defaultdict
from sgmllib import SGMLParser
from xml.dom import minidom, Node
from xml.sax.saxutils import escape, quoteattr

from puddlestuff.tagsources import (write_log, RetrievalError,
    urlopen as _urlopen, parse_searchstring)
from puddlestuff.util import isempty, translate

def urlopen(url):
    print url
    return _urlopen(url)

SERVER = 'http://musicbrainz.org/ws/2/'

TEXT_NODE = Node.TEXT_NODE

ARTISTID = '#artistid'
ALBUMID = '#albumid'
LABELID = '#labelid'
INCLUDES = ''

ARTISTID_FIELD = 'mb_artist_id'
ALBUMID_FIELD = 'mb_album_id'

ARTIST_KEYS = {
    'name': 'artist',
    'sort-name': 'sortname',
    'id': 'artist_id',
    'ext:score': '#score',
    'type': 'artist_type',
    }

ALBUM_KEYS = ARTIST_KEYS.copy()
ALBUM_KEYS.update({
    'name': 'album',
    'id': 'album_id',
    'type': 'album_type',
    'xml:ext': '#xml:ext',
    'title': 'album',
    'track-count': '__numtracks',
    })

TRACK_KEYS = {
    'id': 'mb_track_id',
    'position': 'track',
    'length': '__length',
    }

TO_REMOVE = ('recording', 'offset', 'count')

def children_to_text(node):
    if istext(node): return
    info = dict(node.attributes.items())
    for ch in node.childNodes:
        if istext(ch): continue
        key = ch.tagName
        if key not in info:
            info[key] = node_to_text(ch)
        else:
            info[key] = to_list(info[key], node_to_text(ch))
    return info

def convert_dict(d, fm):
    return dict((fm[k] if k in fm else k, v) for k, v in d.iteritems() if
        not isempty(v))

def fix_xml(xml):
    c = XMLEscaper()
    c.feed(album_xml)
    return c.xml

def istext(node):
    return getattr(node, 'nodeType', None) == TEXT_NODE

def node_to_text(node):
    if len(node.childNodes) > 1:
        return
    text_node = node.firstChild
    if istext(text_node):
        return text_node.data

def parse_album(xml):
    doc = minidom.parseString(xml)
    release_node = doc.getElementsByTagName('release')[0]

    return parse_release(release_node)

def parse_album_search(xml):
    doc = minidom.parseString(xml)
    nodes = doc.getElementsByTagName('release-list')[0].childNodes
    ret = []
    for i, node in enumerate(nodes):
        if istext(node):
            continue
        ret.append(parse_release(node))
    return ret
        
def parse_artist_credit(node):
    artists = parse_node(node, u'artist-credit', u'name-credit', u'artist')
    if not artists:
        return {}

    artist = u', '.join(z[u'artist'][u'name'] for z in artists)
    if len(artists) == 1:
        artist_id = artists[0]['artist']['id']
        return {
            'artist': artist,
            '#artist_id': artist_id,
            'artist_id': artist_id,
            }
    else:
        return {'artist': artist}

def parse_artist_relation(relations):
    ret = defaultdict(lambda: [])
    for r in to_list(relations[u'relation']):
        field = r['type']
        desc = u''

        if u'attribute-list' in r:
            desc = u', '.join(to_list(r[u'attribute-list']['attribute']))
        if u'artist' in r:
            if not desc:
                desc = r[u'artist'][u'name']
            else:
                desc = desc + u' by ' + r[u'artist'][u'name']
        if desc:
            ret[field].append(desc)
    return ret
        
def parse_artist_search(xml):
    doc = minidom.parseString(xml)
    nodes = doc.getElementsByTagName('artist-list')[0].childNodes
    ret = []
    for node in nodes:
        if istext(node):
            continue
        info = dict(node.attributes.items())
        for ch in node.childNodes:
            if istext(node):
                continue
            info[ch.tagName] = node_to_text(ch)
        info = convert_dict(info, ARTIST_KEYS)
        info['#artist_id'] = info['artist_id']
        ret.append(info)
    return ret

def parse_label_list(release_node):
    labels = parse_node(release_node, u'label-info-list', u'label-info',
        u'label')


    catalogs = [z[u'catalog-number'] for z in labels if u'catalog-number' in z]
    label_names = [z[u'label'][u'name'] for z in labels
        if u'label' in z and u'name' in z[u'label']]
    label_ids = [z[u'label'][u'id'] for z in labels
        if u'label' in z and u'id' in z[u'label']]
    return {
        'label': label_names,
        'mb_label_id': label_ids,
        'catalog': catalogs
        }
    
def parse_medium_list(r_node):
    mediums = parse_node(r_node, u'medium-list', u'medium', u'format')
    if not mediums:
        return {}

    mediums = [convert_dict(m, ALBUM_KEYS) for m in mediums]
    info = mediums[0]
    info.update({'discs': unicode(len(mediums))})
    return info

def parse_node(node, header_tag, sub_tag, check_tag):
    ret = []
    nodes = [z for z in node.childNodes if z.tagName == header_tag]
    for node in nodes:
        info = children_to_text(node)
        for ch in node.getElementsByTagName(sub_tag):
            if ch not in node.childNodes:
                continue
            info = info.copy()
            info.update(rec_children_to_text(ch))
            if check_tag not in info:
                continue
            ret.append(info)
    return ret

def parse_recording_relation(relations):
    info = defaultdict(lambda: [])

    for relation in to_list(relations[u'relation']):
        recording = relation['recording']
        desc = None

        if u'artist-credit' in recording:
            artists = []
            for cr in to_list(recording[u'artist-credit']['name-credit']):
                if u'join-phrase' in cr:
                    artists.append(cr[u'join-phrase'])
                artists.append(cr[u'artist'][u'name'])

            unique_artists = []
            for z in artists:
                if z not in unique_artists:
                    unique_artists.append(z)
                
            desc = u' '.join(unique_artists)

        if u'title' in recording:
            if desc:
                desc = recording[u'title'] + u' by ' + desc
            else:
                desc = recording[u'title']
        if desc is not None:
            info[relation['type']].append(desc)
    return info

def parse_release(node):
    info = children_to_text(node)
    info.update(parse_artist_credit(node))
    if len(info['artist']) > 50:
        parse_artist_credit(node)

    info.update(parse_label_list(node))
    info.update(parse_medium_list(node))
    info = convert_dict(info, ALBUM_KEYS)
    info['#album_id'] = info[u'album_id']
    if u'count' in info:
        del(info['count'])
    tracks = []
    for medium in node.getElementsByTagName('medium'):
        tracks.extend(parse_track_list(medium))
    return info, tracks
    
def parse_track_list(node):
    tracks = []
    for t in parse_node(node, 'track-list', 'track', 'position'):
        track = t['recording']
        for k in TO_REMOVE:
            if k in t:
                del(t[k])
        track.update(t)

        if 'puid-list' in track:
            track['musicip_puid'] = track['puid-list']['id']
            del(track['puid-list'])
    
        if u'relation-list' in track and not isempty(track['relation-list']):
            map(track.update,
                map(parse_track_relation, to_list(track['relation-list'])))

        for k, v in track.items():
            if not isinstance(track[k], (basestring, list)):
                del(track[k])
            elif isinstance(v, list) and not isinstance(v[0], basestring):
                del(track[k])

        tracks.append(convert_dict(track, TRACK_KEYS))
    return tracks

def parse_track_relation(relation):
    if relation[u'target-type'] == u'recording':
        return parse_recording_relation(relation)
    elif relation[u'target-type'] == u'artist':
        return parse_artist_relation(relation)
    return {}

def rec_children_to_text(node):
    if istext(node): return
    info = dict(node.attributes.items())
    for ch in node.childNodes:
        if istext(ch):
            continue
        text = node_to_text(ch)
        tag = ch.tagName
        if text is not None:
            info[tag] = to_list(info[tag], text) if tag in info else text
        elif ch.childNodes:
            v = rec_children_to_text(ch)
            info[tag] = to_list(info[tag], v) if tag in info else v
        elif ch.attributes:
            for k, v in ch.attributes.items():
                info[k] = to_list(info[k], v) if k in info else v
    return info

def retrieve_album(album_id):
    url = SERVER + 'release/' + album_id + \
        '?inc=recordings+artist-credits+puids+isrcs+tags+ratings' \
        '+artist-rels+recording-rels+release-rels+release-group-rels' \
        '+url-rels+work-rels+recording-level-rels+work-level-rels'

    xml = urlopen(url)
    f = open('this_is_it.xml', 'w')
    f.write(xml)
    f.close()
    return parse_album(xml)
    
def search_album(album=None, artist=None, limit=25, offset=0, own=False):
    if own:
        if isinstance(album, unicode):
            album = album.encode('utf8').replace(':', '')

        return SERVER + 'release/?query=' + urllib.quote_plus(album) + \
            '&limit=%d&offset=%d' % (limit, offset)

    if artist:
        if isinstance(artist, unicode):
            artist = artist.encode('utf8')
        query = 'artistname:' + urllib.quote_plus(artist)

    if album:
        if isinstance(album, unicode):
            album = album.encode('utf8')
        if artist:
            query = 'release:' + urllib.quote_plus(album) + \
                '%20AND%20' + query
        else:
            query = 'release:' + urllib.quote_plus(album)

    return SERVER + 'release/?query=' + query.replace('%3A', '') + \
        '&limit=%d&offset=%d' % (limit, offset)

def search_artist(artist, limit=25, offset=0):
    if isinstance(artist, unicode):
        artist.encode('utf8')
    query = urllib.urlencode({
        'query': artist,
        'limit': limit,
        'offset': offset,
        })
    return SERVER + 'artist?' + query.replace('%3A', '')

def to_list(v, arg=None):
    if isinstance(v, list):
        if arg is not None:
            v.append(arg)
        return v
    else:
        return [v, arg] if arg is not None else [v]

class XMLEscaper(SGMLParser):
    def reset(self):
        SGMLParser.reset(self)
        self._xml = []

    def handle_data(self, data):
        self._xml.append(escape(data))

    def unknown_starttag(self, tag, attributes):
        attrib_str = ' '.join('%s=%s' % (k, quoteattr(v))
            for k, v in attributes)
        self._xml.append('<%s %s>' % (tag, attrib_str))

    def unknown_endtag(self, tag):
        self._xml.append('</%s>' % tag)

    def _get_xml(self):
        return ''.join(self._xml)

    xml = property(_get_xml)


class MusicBrainz(object):
    name = 'MusicBrainz (NGS)'

    group_by = [u'album', 'artist']
    def __init__(self):
        super(MusicBrainz, self).__init__()
        self.__lasttime = time.time()

    def keyword_search(self, s):
        if s.startswith(u':a'):
            artist_id = s[len(':a'):].strip()
            try:
                xml = urlopen(search_album(u'arid:' + artist_id,
                    limit=100, own=True))
                return parse_album_search(xml)
            except RetrievalError, e:
                msg = translate("MusicBrainz",
                    '<b>Error:</b> While retrieving %1: %2')
                write_log(msg.arg(artist_id).arg(escape(e)))
                raise
        elif s.startswith(u':b'):
            r_id = s[len(u':b'):].strip()
            try:
                return [retrieve_album(r_id)]
            except RetrievalError, e:
                msg = translate("MusicBrainz",
                    "<b>Error:</b> While retrieving Album ID %1 (%2)")
                write_log(msg.arg(r_id).arg(escape(e)))
                raise 
        else:
            try:
                params = parse_searchstring(s)
            except RetrievalError, e:
                return parse_album_search(urlopen(search_album(s, limit=100)))
            if not params:
                return
            artist = params[0][0]
            album = params[0][1]
            return self.search(album, [artist], 100)

    def search(self, album, artists=u'', limit=40):
        if time.time() - self.__lasttime < 1000:
            time.sleep(1)

        ret = []
        check_matches = False
        if isempty(artists):
            artist = None
        if len(artists) > 1:
            artist = u'Various Artists'
        elif artists:
            if hasattr(artists, 'items'):
                artist = artists.keys()[0]
            else:
                artist = artists[0]

        if not album and not artist:
            raise RetrievalError('Album or Artist required.')

        write_log(u'Searching for %s' % album)
        try:
            xml = urlopen(search_album(album, artist, limit))
        except urllib2.URLError, e:
            write_log(u'Error: While retrieving search page %s' %
                        unicode(e))
            raise RetrievalError(unicode(e))
        write_log(u'Retrieved search results.')
        self.__lasttime = time.time()
        return parse_album_search(xml)

    def retrieve(self, albuminfo):
        album_id = albuminfo['#album_id']
        if time.time() - self.__lasttime < 1000:
            time.sleep(1)
        ret = retrieve_album(album_id)
        self.__lasttime = time.time()
        return ret

info = MusicBrainz

if __name__ == '__main__':
    #c = MusicBrainz()
    xml = open('this_is_it.xml', 'r').read()
    #x = c.search('New Again', 'Taking Back Sunday')
    x = parse_album(xml)
    #y = parse_tracks(open('taking_tracks.xml', 'r').read())
    #print c.retrieve(x[0][0])