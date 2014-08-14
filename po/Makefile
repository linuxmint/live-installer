#!/usr/bin/make -f

package=live-installer

.PHONY: all build clean update

all: build

# create mo from the po files
%.mo : %.po
	mkdir -p mo/$(subst .po,,$<)/LC_MESSAGES/
	msgfmt $< -o mo/$(subst .po,,$<)/LC_MESSAGES/$(package).mo

# generate all *.mo files
build: update $(patsubst %.po,%.mo,$(wildcard *.po))

clean:
	rm -rf mo

# update *.pot and merge updates into *.po files
update:
	# update template.pot
	@ xgettext \
		--keyword=_ \
		--language=Python \
		--output=$(package).pot \
		--package-name=$(package) \
		--package-version='' \
		--from-code=UTF-8 \
		`find .. -name '*.py'` && echo OK
	# get strings from the desktop file
	@ for LAUNCHER in `find .. -name '*.desktop'`; do \
		sed -r \
			-e '/^(Name|Comment|GenericName|Keywords)\[/d' \
			-e 's/^(Comment=|Name=|GenericName=|Keywords=)/_\1/' \
			$$LAUNCHER > $$LAUNCHER.in ; \
		intltool-extract --type=gettext/ini $$LAUNCHER.in ; \
		xgettext \
			--join-existing \
			--keyword=N_:1 \
			--output $(package).pot \
			$$LAUNCHER.in.h ; \
	done && echo OK
	# merge new strings with translations
	@ for POFILE in $(wildcard *.po); do \
		msgmerge --quiet --backup=none --update $$POFILE $(package).pot ; \
	done && echo OK
	# apply desktop file modifications
	@ for LAUNCHER in `find .. -name '*.desktop'`; do \
		intltool-merge --quiet --desktop-style . $$LAUNCHER.in $$LAUNCHER ; \
		rm $$LAUNCHER.in.h $$LAUNCHER.in ; \
	done && echo OK
