#!/usr/bin/env python

import os
import gtk
import glib
import time
import string
import threading

pages = [
'welcome.html',
'giants.html',
'desktop-ready.html',
'windows.html',
'safe.html',
'community.html'
]

class Slideshow(threading.Thread):
    def __init__(self, webviewObject, slideshowDirectory, language='', intervalSeconds=30, loopPages=False):
        threading.Thread.__init__(self)
        self.browser = webviewObject
        self.loop = loopPages
        self.interval = intervalSeconds
        self.language = language
        self.slideshowDir = slideshowDirectory
        self.languageDir = self.getLanguageDirectory()
        self.template = os.path.join(slideshowDirectory, 'template.html')
        self.templateText = ''
        self.pageContent = []
        
        # Prepare variables
        self.prepare()
        
    
    def prepare(self):
        try:
            # Prepare pages
            if os.path.isfile(self.template):
                print 'Template path: ' + self.template
                tmplFile = open(self.template,'r')
                self.templateText = tmplFile.read()
                tmplFile.close()

                # Preload all pages in an array
                chkString = '<div id="container">'
                if chkString in self.templateText:
                    for page in pages:
                        # Open content file
                        pagePath = os.path.join(self.languageDir, page)
                        if os.path.isfile(pagePath):
                            contFile = open(pagePath, 'r')
                            # Merge content with template
                            html = string.replace(self.templateText, chkString, chkString + contFile.read())
                            self.pageContent.append([pagePath, html])
                            contFile.close()
                        else:
                            print 'Content path does not exist: ' + pagePath
                else:
                    print 'Check string not found in template: ' + chkString
            else:
                print 'Template path not found: ' + self.template
        except Exception, detail:
            print detail

    def run(self):
        # Update widget in main thread             
        try:
            if self.pageContent:
                # Loop through all pages
                lastIndex = len(self.pageContent) - 1
                runLoop = True
                i = 0
                while runLoop:
                    # Get the full path of the content page
                    if os.path.isfile(self.pageContent[i][0]):
                        #print 'Load page: ' + self.pageContent[i][0]
                        # Load html into browser object
                        # Use glib to schedule an update of the parent browser object
                        # If you do this directly the objects won't refresh
                        glib.idle_add(self.browser.load_html_string, self.pageContent[i][1], 'file:///')
                        
                        # Wait interval
                        time.sleep(self.interval)
                        
                        # Reset counter when you need to loop the pages
                        if i == lastIndex:
                            if self.loop:
                                i = 0
                            else:
                                runLoop = False
                        else:
                            i = i + 1
                    else:
                        # You can only get here if you delete a file while in the loop
                        print 'Page not found: ' + self.pageContent[i][0]
            else:
                print 'No pages found to load'
        except Exception, detail:
            print detail
            
    def getLanguageDirectory(self):
        langDir = self.slideshowDir
        if self.language != '':
            testDir = os.path.join(self.slideshowDir, 'loc.' + self.language)
            if os.path.exists(testDir):
                langDir = testDir
            else:
                if "_" in self.language:
                    split = self.language.split("_")
                    if len(split) == 2:
                        testDir = os.path.join(self.slideshowDir, 'loc.' + split[0])
                        if os.path.exists(testDir):
                            langDir = testDir
        return langDir
