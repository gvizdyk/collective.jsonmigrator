import base64
import simplejson
import threading
import time
from DateTime import DateTime
import urllib
import urllib2
from zope.interface import classProvides, implements
from collective.transmogrifier.interfaces import ISectionBlueprint
from collective.transmogrifier.interfaces import ISection
from collective.jsonmigrator import logger
import requests

class CatalogSourceSection(object):
    """A source section which creates items from a remote Plone site by
       querying it's catalog.
    """
    classProvides(ISectionBlueprint)
    implements(ISection)

    def __init__(self, transmogrifier, name, options, previous):
        self.previous = previous
        self.options = options
        self.context = transmogrifier.context

        self.remote_url = self.get_option('remote-url',
                                          'http://localhost:8080')
        remote_username = self.get_option('remote-username', 'admin')
        remote_password = self.get_option('remote-password', 'admin')

        catalog_path = self.get_option('catalog-path', '/Plone/portal_catalog')
        self.site_path_length = len('/'.join(catalog_path.split('/')[:-1]))

        catalog_query = self.get_option('catalog-query', None)
        catalog_query = ' '.join(catalog_query.split())
        catalog_query = base64.b64encode(catalog_query)

        self.remote_skip_paths = self.get_option('remote-skip-paths',
                                                 '').split()
        self.queue_length = int(self.get_option('queue-size', '10'))

        # Install a basic auth handler
        auth_handler = urllib2.HTTPBasicAuthHandler()
        auth_handler.add_password(realm='Zope',
                                  uri=self.remote_url,
                                  user=remote_username,
                                  passwd=remote_password)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)

        req = urllib2.Request('%s%s/get_catalog_results' % (self.remote_url,
            catalog_path), urllib.urlencode({'catalog_query': catalog_query}))
        self.session = requests.Session()
        self.session.auth =(remote_username, remote_password)
        self.session.headers.update({'x-test': 'true'})
        resp = self.session.get('%s%s/get_catalog_results' % (self.remote_url,catalog_path),
            params={'catalog_query': catalog_query},verify=False).content
 

        self.item_paths = sorted(simplejson.loads(resp))

    def get_option(self, name, default):
        """Get an option from the request if available and fallback to the
        transmogrifier config.
        """
        request = getattr(self.context, 'REQUEST', None)
        if request is not None:
            value = request.form.get('form.widgets.'+name.replace('-', '_'),
                                     self.options.get(name, default))
        else:
            value = self.options.get(name, default)
        if isinstance(value, unicode):
            value = value.encode('utf8')
        return value

    def __iter__(self):
        for item in self.previous:
            yield item

        queue = QueuedItemLoader(self.remote_url, self.item_paths,
                                 self.remote_skip_paths, self.queue_length,
                                 self.session)
        queue.start()

        for item in queue:
            if not item:
                continue

            item['_path'] = str(item['_path'][self.site_path_length:])
            if item.has_key('query'):
                res = []
                for query in item['query']:
                    qq = {}
                    for opt in query:
                        qq[opt[0]] = opt[1]
                    res.append(qq)
                item['query'] = res
            if item.has_key('effectiveDate'):
                item['effective'] = str(item['effectiveDate'])
            if item.has_key('expirationDate'):
                item['expires'] = str(item['expirationDate'])
            if item.has_key('allowDiscussion'):
                item['allow_discussion'] = item['allowDiscussion']
            if item.has_key('excludeFromNav'):
                item['exclude_from_nav'] = item['excludeFromNav']
            if item.has_key('subject'):
                item['subjects'] = item['subject']
            if item.has_key('_atrefs'):
                item['relatedItems'] = item['_atrefs']
            if item.has_key('startDate'):
                item['start'] = DateTime(item['startDate']).utcdatetime()
            if item.has_key('endDate'):
                item['end'] = DateTime(item['endDate']).utcdatetime()
            yield item


class QueuedItemLoader(threading.Thread):

    def __init__(self, remote_url, paths, remote_skip_paths, queue_length, session):
        super(QueuedItemLoader, self).__init__()

        self.remote_url = remote_url
        self.paths = list(paths)
        self.remote_skip_paths = remote_skip_paths
        self.queue_length = queue_length

        self.queue = []
        self.finished = len(paths) == 0
        self.session = session


    def __iter__(self):
        while not self.finished or len(self.queue) > 0:
            while len(self.queue) == 0:
                time.sleep(0.0001)

            yield self.queue.pop(0)

    def run(self):
        while not self.finished:
            while len(self.queue) >= self.queue_length:
                time.sleep(0.0001)

            path = self.paths.pop(0)
            if not self._skip_path(path):
                item = self._load_path(path)
                self.queue.append(item)

            if len(self.paths) == 0:
                self.finished = True

    def _skip_path(self, path):
        for skip_path in self.remote_skip_paths:
            if path.startswith(skip_path):
                return True
        return False

    def _load_path(self, path):
        item_url = '%s%s/get_item' % (self.remote_url, urllib.quote(path))

        try:
            #f = urllib2.urlopen(item_url)
            item_json = self.session.get(item_url, verify=False).content #json() #f.read()
        except urllib2.URLError, e:
            logger.error("Failed reading item from %s. %s" % (item_url, str(e)))
            return None
        try:
            item = simplejson.loads(item_json)
        except simplejson.JSONDecodeError:
            logger.error("Could not decode item from %s." % item_url)
            return None
        return item
