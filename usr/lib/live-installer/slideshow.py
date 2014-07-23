#!/usr/bin/env python

import os
import glib
import glob
import time
from threading import Thread

SLIDESHOW_DIR = "/usr/share/live-installer/slideshow"

class Slideshow(Thread):
    def __init__(self, webview, language, basedir=SLIDESHOW_DIR,
                 interval_seconds=30, loop_pages=True):
        Thread.__init__(self)
        self.browser = webview
        self.basedir = basedir
        self.interval = interval_seconds
        self.loop = loop_pages
        self.slideshow_pages = []
        self.daemon = True  # let the thread die with the parent
        try:
            with open(os.path.join(self.basedir, 'template.html')) as f:
                template = f.read()
        except IOError:
            print 'WARNING: Slideshow template not found.'
            return
        # Preload all pages in an array
        lang_dir = self.get_language_dir(language)
        for page in sorted(glob.iglob(os.path.join(lang_dir, '*.html'))):
            with open(page) as f:
                self.slideshow_pages.append(template.format(f.read()))

    def run(self):
        if not self.slideshow_pages: return  # prevent busy-looping if no pages
        # Update webview in the main thread
        while True:
            for page in self.slideshow_pages:
                # Use glib to schedule an update of the browser object
                # If you do this directly, the objects won't refresh
                glib.idle_add(self.browser.load_string,
                              page, 'text/html', 'UTF-8', 'file://{}/'.format(self.basedir))
                time.sleep(self.interval)
            if not self.loop:
                break

    def get_language_dir(self, lang):
        # First test if full locale directory exists, e.g. slideshow/pt_BR,
        # otherwise perhaps at least the language is there, e.g. slideshow/fi
        for lang in (lang, lang.split('_')[0]):
            path = os.path.join(self.basedir, lang)
            if lang and os.path.isdir(path):
                return path
        # else, just return English slides
        return os.path.join(self.basedir, 'en')
