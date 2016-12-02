# -*- coding: utf-8 -*-
from collective.jsonmigrator import logger
from collective.transmogrifier.interfaces import ISection
from collective.transmogrifier.interfaces import ISectionBlueprint
from zope.interface import classProvides, implements

import base64
import threading
import time
import urllib
import urllib2
import requests

try:
    import json
except ImportError:
    import simplejson as json


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
         
        """
        # Install a basic auth handler
        auth_handler = urllib2.HTTPBasicAuthHandler()
        auth_handler.add_password(realm='Zope',
                                  uri=self.remote_url,
                                  user=remote_username,
                                  passwd=remote_password)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)
        """

        self.session = requests.Session()
        self.session.auth =(remote_username, remote_password)
        self.session.headers.update({'x-test': 'true'})
        self.item_paths = []
        
        url = '%s%s/get_catalog_results' % (self.remote_url,catalog_path)
        resp = self.session.get(url, params={'catalog_query': catalog_query}, verify=False)
        data = resp.content
        #import pdb;pdb.set_trace()
        #self.item_paths = sorted(simplejson.loads(data))
        """
        req = urllib2.Request(
            '%s%s/get_catalog_results' %
            (self.remote_url, catalog_path), urllib.urlencode(
                {
                    'catalog_query': catalog_query}))
        try:
            f = urllib2.urlopen(req)
            resp = f.read()
        except urllib2.URLError:
            raise

        self.item_paths = sorted(simplejson.loads(resp))
        """
        self.item_paths = sorted(json.loads(data))


    def get_option(self, name, default):
        """Get an option from the request if available and fallback to the
        transmogrifier config.
        """
        request = getattr(self.context, 'REQUEST', None)
        if request is not None:
            value = request.form.get('form.widgets.' + name.replace('-', '_'),
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

            item['_path'] = item['_path'][self.site_path_length:]
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
            item_json = self.session.get(item_url, verify=False).content
        except urllib2.URLError as e:
            logger.error(
                "Failed reading item from %s. %s" %
                (item_url, str(e)))
            return None
        try:
            item = json.loads(item_json)
        except:
            logger.error("Could not decode item from %s." % item_url)
            return None

        if item['_type'] in ['SimpleVocabulary', 'SimpleTranslatedVocabularyTerm', 'VocabularyLibrary']:
            return None
        if item['_path'].find('portal_vocabulary') > 0:
            return None
        if item.has_key('start_date'):
            item['start'] = item['start_date']
            item['end'] = item['end_date']
        if item.has_key('audiences'):
            item['taxonomy_audiences'] = item['audiences']
            #import pdb;pdb.set_trace()
        if item.has_key('topics'):
            item['taxonomy_topics'] = item['topics']
        if item.has_key('programs'):
            item['taxonomy_programs'] = item['programs']
        if item.has_key('services'):
            item['taxonomy_services'] = item['services']
        if item.has_key('regions'):
            item['taxonomy_regions'] = item['regions']
        return item
