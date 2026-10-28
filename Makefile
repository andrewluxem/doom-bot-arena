# Match runs and benches need ViZDoom >= 1.3.0 (Linux pin in requirements-linux.txt).
# On the Intel mac use the docker-* targets; they run the same commands in the pinned image.
# make test needs only pytest (requirements-dev.txt) on top of either requirements file.

PYTHON ?= python3
SEED ?= 1234
TICS ?= 2100
BUNDLE ?=
IMAGE ?= doom-bot-arena:vizdoom-1.3.2
DOCKER_RUN = docker run --rm --platform linux/amd64 -v "$(CURDIR)":/repo $(IMAGE)

.PHONY: test bench bench-smoke bench-controlled bench-spawn match validate image docker-bench docker-match docker-validate

test:
	$(PYTHON) -m pytest -q tests

bench: bench-smoke bench-controlled

bench-smoke:
	$(PYTHON) -m engine.bench.smoke --seed $(SEED) --out out/bench

bench-controlled:
	$(PYTHON) -m engine.bench.controlled --seed $(SEED) --out out/bench

bench-spawn:
	$(PYTHON) -m engine.bench.spawn_telefrags --seeds 1-10 --out out/bench

match:
	$(PYTHON) -m engine.runner --duration-tics $(TICS) --seed $(SEED) --out bundles

validate:
	$(PYTHON) -m engine.validate_bundle $(BUNDLE)

image:
	docker build --platform linux/amd64 -f docker/Dockerfile -t $(IMAGE) .

docker-bench:
	$(DOCKER_RUN) make bench SEED=$(SEED)

docker-match:
	$(DOCKER_RUN) make match TICS=$(TICS) SEED=$(SEED)

docker-validate:
	$(DOCKER_RUN) make validate BUNDLE=$(BUNDLE)
