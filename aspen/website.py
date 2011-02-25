import datetime
import logging
import os
import re
import sys
import traceback
import urlparse
from os.path import join, isfile, isdir, dirname

from aspen import simplates
from aspen.http import Request, Response


log = logging.getLogger('aspen.website')
find_ours = lambda s: join(os.path.dirname(__file__), 'www', s)


class Website(object):
    """Represent a website.
    """

    def __init__(self, configuration):
        self.configuration = configuration
        self.root = configuration.root
        self.hooks = configuration.hooks
        self.show_tracebacks = configuration.conf.aspen.no('show_tracebacks')

    def __call__(self, diesel_request):
        """Main diesel handler.
        """
        try:
            request = Request(diesel_request) # too big to fail :-/
            try:
                request.configuration = self.configuration
                request.conf = self.configuration.conf
                request.root = self.configuration.root
                request.fs = self.translate(request)
                for hook in self.hooks.inbound:
                    request = hook(request) or request
                simplates.handle(request)
            except:
                try:
                    first_tb = traceback.format_exc()
                    response = sys.exc_info()[1]
                    if not isinstance(response, Response):
                        tb = traceback.format_exc()
                        log.error(tb)
                        response = Response(500, tb)
                    response.request = request
                    response.cookie = request.cookie
                    for hook in self.hooks.outbound:
                        response = hook(response) or response
                    if response.code == 200:
                        raise
                    self.nice_error(request, response)
                except:
                    if sys.exc_info()[0] is Response:
                        raise # normal


                    # Last chance for a traceback.
                    # ============================

                    tb = traceback.format_exc().strip()
                    tbs = '\n\n'.join([tb, "... while handling ...", first_tb])
                    log.error(tbs)
                    if self.show_tracebacks:
                        raise Response(500, tbs)
                    else:
                        raise Response(500)

            else:
                raise Response(500)

        except Response, response:
            response.headers.set('Content-Length', len(response.body))
            response.cookie = {}
            self.log_access(request, response) # TODO this at the right level?
            return response._to_diesel(diesel_request)

    def nice_error(self, request, response):
        fs = str(response.code) + '.html'
        theirs = join(request.root, '.aspen', 'etc', 'templates', fs)
        ours = find_ours(fs)
        if isfile(theirs):
            request.fs = theirs
        elif isfile(ours):
            request.fs = ours
        else:
            raise
        simplates.handle(request, response)

    def translate(self, request):
        """Given a Request, return a filesystem path, or raise Response.
        """
       
        # First step.
        # ===========
        # We specifically avoid removing symlinks in the path so that the
        # filepath remains under the website root. Also, we don't want 
        # trailing slashes for directories in fs.

        parts = [self.root] + request.path.lstrip('/').split('/')
        request.fs = os.sep.join(parts).rstrip(os.sep)


        # Gauntlet
        # ========
        # We keep request.fs up to date for logging purposes.

        if request.fs.startswith('.'):          # hidden files
            raise Response(404)

        if isdir(request.fs):                   # trailing slash
            if not request.path.endswith('/'):
                parts = list(request.urlparts)
                parts[2] += '/'
                location = urlparse.urlunparse(parts)
                raise Response(301, "Moved", {'Location': location})

        if isdir(request.fs):                   # index 
            index = join(request.fs, 'index.html')
            if isfile(index):
                request.fs = index

        if isdir(request.fs):                   # auto index
            if not self.configuration.autoindex: # or not
                raise Response(404)
            request.headers.set('X-Aspen-AutoIndexDir', request.fs)
            request.fs = find_ours('index.html') 

        if '.sock/' in request.fs:
            parts = request.fs.split('.sock/')
            assert len(parts) == 2
            request.fs = parts[0] + '.sock'
            sockinfo = parts[1].split('/')
            ninfo = len(sockinfo)
            if ninfo >= 1:
                request.transport = sockinfo[0]
            if ninfo >= 2:
                request.session_id = sockinfo[1]
            if ninfo >= 3:
                pass # what is this?

        if not isfile(request.fs):              # genuinely not found
            if request.path == '/favicon.ico':  # special case
                request.fs = find_ours('favicon.ico')
            else:
                raise Response(404)


        # Now you are one of us.
        # ======================

        return request.fs

    def log_access(self, request, response):
        """Log access.
        """

        # What was the URL path translated to?
        # ====================================

        fs = request.fs[len(self.root):]
        if fs:
            fs = '.'+fs
        else:
            fs = request.fs
        log.info("%s => %s" % (request.path, fs))


        # Where was response raised from?
        # ===============================

        tb = sys.exc_info()[2]
        while tb.tb_next is not None:
            tb = tb.tb_next
        frame = tb.tb_frame
        co = tb.tb_frame.f_code
        filename = tb.tb_frame.f_code.co_filename
        if filename.startswith(self.root):
            filename = '.'+filename[len(self.root):]
        log.info("%33s  %s:%d" % ( '<%s>' % response
                                 , filename
                                 , frame.f_lineno
                                  ))

