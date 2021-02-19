DESTDIR=/

all: clean build

build: buildmo
	mkdir -p build/usr/lib/ || true

	cp -prfv live-installer build/usr/lib/

	#set parmissions
	chmod 755 -R build
	chown root -R build

buildmo:
	mkdir -p build/usr/share/ || true
	@echo "Building the mo files"
	# WARNING: the second sed below will only works correctly with the languages that don't contain "-"
	for file in `ls po/*.po`; do \
		lang=`echo $$file | sed 's@po/@@' | sed 's/\.po//' | sed 's/live-installer-//'`; \
		install -d build/usr/share/locale/$$lang/LC_MESSAGES/; \
		msgfmt -o build/usr/share/locale/$$lang/LC_MESSAGES/live-installer.mo $$file; \
	done \

install:
	cp -prfv build/* $(DESTDIR)/
	mkdir -p $(DESTDIR)/usr/share/applications/ || true
	mkdir -p $(DESTDIR)/usr/bin/ || true
	install live-installer.desktop $(DESTDIR)/usr/share/applications/live-installer.desktop
	mkdir -p $(DESTDIR)/etc/X11/Xsession.d/ || true
	install 00-live $(DESTDIR)/etc/X11/Xsession.d/00-live
	install live-installer.sh $(DESTDIR)/usr/bin/live-installer

uninstall:
	rm -rf $(DESTDIR)/usr/lib/live-installer
	rm -f $(DESTDIR)/usr/bin/live-installer
	rm -f $(DESTDIR)/usr/share/applications/live-installer.desktop
clean:
	rm -rf build
	find po/ | grep "*.mo" | xargs rm -f
