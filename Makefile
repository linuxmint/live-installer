DESTDIR=/
XINITRCDIR=/etc/X11/Xsession.d/
all: clean build

build: buildmo
	mkdir -p build/lib/ || true
	cp -prfv live-installer build/lib/
	@if [  -d custom/configs ]; then \
	    cp -prfv custom/configs build/lib/live-installer/; \
	fi
	@if [ -d custom/branding ]; then \
	    rm -rf build/lib/live-installer/branding/slides/; \
	    cp -prfv custom/branding build/lib/live-installer/; \
	fi
	chmod +x -R build/lib/

run: build
	build/lib/live-installer/main.py

pot:
	xgettext -o live-installer.pot --from-code="utf-8" live-installer/resources/*.ui `find live-installer -type f -iname "*.py"`
	for file in `ls po/*.po`; do \
            msgmerge $$file live-installer.pot -o $$file.new ; \
	    rm -f $$file ; \
	    mv $$file.new $$file ; \
	done \

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
	rm -rf  $(DESTDIR)/lib/live-installer || true
	mkdir -p $(DESTDIR)/lib || true
	mkdir -p $(DESTDIR)/usr || true
	cp -prfv build/usr/* $(DESTDIR)/usr/
	cp -prfv build/lib/* $(DESTDIR)/lib/
	mkdir -p $(DESTDIR)/usr/share/applications/ || true
	mkdir -p $(DESTDIR)/usr/bin/ || true
	mkdir -p $(DESTDIR)/etc/xdg/autostart/
	mkdir -p $(DESTDIR)/usr/share/icons/hicolor/scalable/status/
	mkdir -p $(DESTDIR)/$(XINITRCDIR) || true
	mkdir -p $(DESTDIR)/usr/share/polkit-1/actions/ || true
	mkdir -p $(DESTDIR)/usr/share/icons/hicolor/symbolic/status/ || true
	install data/17g-welcome.desktop $(DESTDIR)/etc/xdg/autostart/
	install data/live-installer.desktop $(DESTDIR)/usr/share/applications/live-installer.desktop
	install data/00-live $(DESTDIR)/$(XINITRCDIR)/00-live
	install data/live-installer.sh $(DESTDIR)/usr/bin/live-installer
	install data/org.17g.installer.policy $(DESTDIR)/usr/share/polkit-1/actions/
	@if [ -f custom/live-installer.desktop ] ; then \
	    install custom/live-installer.desktop $(DESTDIR)/usr/share/applications/live-installer.desktop ; \
	fi
	install live-installer/resources/icons/symbolic/*.svg $(DESTDIR)/usr/share/icons/hicolor/scalable/status/

install-systemd:
	mkdir -p $(DESTDIR)/lib/systemd/system/
	install 17g.service $(DESTDIR)/lib/systemd/system/

install-openrc:
	mkdir -p $(DESTDIR)/lib/init.d/
	install 17g.initd $(DESTDIR)/$(DESTDIR)/lib/init.d/17g

uninstall:
	rm -rf $(DESTDIR)/lib/live-installer
	rm -f $(DESTDIR)/usr/bin/live-installer
	rm -f $(DESTDIR)/usr/share/applications/live-installer.desktop
	rm -f $(DESTDIR)/$(XINITRCDIR)/00-live
	rm -f $(DESTDIR)/usr/share/polkit-1/actions/org.17g.installer.policy
	rm -f $(DESTDIR)/etc/xdg/autostart/17g-welcome.desktop
clean:
	rm -rf build
	find po/ | grep "*.mo" | xargs rm -f
	rm -rf live-installer/__pycache__
	rm -rf live-installer/frontend/__pycache__
