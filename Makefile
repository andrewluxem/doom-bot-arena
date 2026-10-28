# Match runs and benches need ViZDoom >= 1.3.0 (Linux pin in requirements-linux.txt).
# On the Intel mac use the docker-* targets; they run the same commands in the pinned image.
# make test needs only pytest (requirements-dev.txt) on top of either requirements file.

PYTHON ?= python3
SEED ?= 1234
TICS ?= 2100
BUNDLE ?=
IMAGE ?= doom-bot-arena:vizdoom-1.3.2
DOCKER_RUN = docker run --rm --platform linux/amd64 -v "$(CURDIR)":/repo $(IMAGE)

SEEDS ?= 101 102 103 104 105
EVAL_OUT ?= bundles/eval-2a
BOTS ?= VEX,HEX,NUL

.PHONY: test bench bench-smoke bench-controlled bench-personality bench-spawn match validate eval signatures image docker-test docker-bench docker-match docker-validate docker-eval

test:
	$(PYTHON) -m pytest -q tests

bench: bench-smoke bench-controlled bench-personality

bench-smoke:
	$(PYTHON) -m engine.bench.smoke --seed $(SEED) --out out/bench

bench-controlled:
	$(PYTHON) -m engine.bench.controlled --seed $(SEED) --out out/bench

bench-personality:
	$(PYTHON) -m engine.bench.personality --seed $(SEED) --out out/bench

bench-spawn:
	$(PYTHON) -m engine.bench.spawn_telefrags --seeds 1-10 --out out/bench

match:
	$(PYTHON) -m engine.runner --duration-tics $(TICS) --seed $(SEED) --out bundles

validate:
	$(PYTHON) -m engine.validate_bundle $(BUNDLE)

# Milestone 2A evaluation: five-minute matches on MAP01 at fixed seeds, then the signature report.
eval:
	for s in $(SEEDS); do $(PYTHON) -m engine.runner --duration-tics 10500 --seed $$s --out $(EVAL_OUT) || exit 1; done
	$(PYTHON) tools/signatures.py --bots $(BOTS) --json $(EVAL_OUT)/signatures.json $(EVAL_OUT)/2*

signatures:
	$(PYTHON) tools/signatures.py --bots $(BOTS) $(BUNDLE)

image:
	docker build --platform linux/amd64 -f docker/Dockerfile -t $(IMAGE) .

docker-test:
	$(DOCKER_RUN) make test

docker-bench:
	$(DOCKER_RUN) make bench SEED=$(SEED)

docker-match:
	$(DOCKER_RUN) make match TICS=$(TICS) SEED=$(SEED)

docker-validate:
	$(DOCKER_RUN) make validate BUNDLE=$(BUNDLE)

docker-eval:
	$(DOCKER_RUN) make eval SEEDS="$(SEEDS)" EVAL_OUT=$(EVAL_OUT)
