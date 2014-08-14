#!/usr/bin/make -f

.PHONY: all build clean

all: build

clean:
	# clean i18n
	(cd po && $(MAKE) clean)
	(cd slideshow && $(MAKE) clean)

build:
	# build i18n
	(cd po && $(MAKE))
	(cd slideshow && $(MAKE))
