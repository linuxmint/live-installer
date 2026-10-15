PO_FILES = $(wildcard po/live-installer-*.po)
MO_FILES = $(patsubst po/live-installer-%.po,usr/share/locale/%/LC_MESSAGES/live-installer.mo,$(PO_FILES))

all: $(MO_FILES)

usr/share/locale/%/LC_MESSAGES/live-installer.mo: po/live-installer-%.po
	install -d $(dir $@)
	msgfmt -o $@ $<

clean:
	rm -rf usr/share/locale
