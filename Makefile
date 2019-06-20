DESTDIR =
PREFIX = /usr/local
BINDIR = $(PREFIX)/bin

# test defaults
TEST = 00basic
STAGE = gentoo/stage3-amd64
EPYTHON = python3.6
LANG = C.UTF-8

# set this to command used to prune docker images if necessary
DOCKER_CLEANUP =

all:
clean:
distclean:

check-docker:
	docker build -f docker/$(TEST)/Dockerfile \
		--build-arg STAGE=$(STAGE) \
		--build-arg EPYTHON=$(EPYTHON) \
		--build-arg LANG=$(LANG) .

check:
	# check multilib & no-multilib profiles
	+$(MAKE) check-docker
	+$(MAKE) STAGE=gentoo/stage3-amd64-nomultilib check-docker
	# regression tests
	+$(MAKE) TEST=00abslibsymlink check-docker
	+$(MAKE) TEST=00rmkeepfiles check-docker
	# check python2.7
	+$(MAKE) EPYTHON=python2.7 check-docker
	$(DOCKER_CLEANUP)
	# check whether utf-8 filenames don't kill it
	+$(MAKE) TEST=01utf8 check-docker
	+$(MAKE) EPYTHON=python2.7 TEST=01utf8 check-docker
	+$(MAKE) LANG=C TEST=01utf8 check-docker
	+$(MAKE) LANG=C EPYTHON=python2.7 TEST=01utf8 check-docker
	$(DOCKER_CLEANUP)
	# check whether invalid utf-8 filenames don't kill it
	+$(MAKE) TEST=02nonutf8 check-docker
	+$(MAKE) EPYTHON=python2.7 TEST=02nonutf8 check-docker
	+$(MAKE) LANG=C TEST=02nonutf8 check-docker
	+$(MAKE) LANG=C EPYTHON=python2.7 TEST=02nonutf8 check-docker
	$(DOCKER_CLEANUP)

install:
	install -d $(DESTDIR)$(BINDIR)
	install -m0755 unsymlink-lib $(DESTDIR)$(BINDIR)/

.PHONY: all check clean distclean install
