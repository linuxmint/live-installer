.PHONY: build clean all

all: build

clean:
	(po4a --rm-translations po4a/po4a.cfg)
	rm -rf translated

build:
	@ po4a po4a/po4a.cfg
	# don't ship incomplete translations
	@ n_required=`ls en | wc -l`; \
	for dir in `echo translated/*`; do \
	    n_slides=`ls $$dir | wc -l`; \
	    [ $$n_slides != $$n_required ] && rm -rf $$dir; \
	done >/dev/null || true
	@ echo "Shipped slideshow translations (`ls translated | wc -l`):" `ls translated`
