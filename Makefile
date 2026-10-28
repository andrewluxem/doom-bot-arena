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
PERSONALITIES ?= VEX,HEX,NUL

# Milestone 2B: the placeholder baseline per map, and the eight personalities on two maps.
BASE_MAPS ?= MAP01 MAP10 E1M5
BASE_SEEDS ?= 201 202 203 204 205
BASE_OUT ?= bundles/baseline-2b
EVAL2B_MAPS ?= MAP10 E1M5
EVAL2B_SEEDS ?= 301 302 303 304 305
EVAL2B_OUT ?= bundles/eval-2b

.PHONY: test bench bench-smoke bench-controlled bench-personality bench-spawn bench-items match validate eval baseline eval-2b signatures-2b signatures image docker-test docker-bench docker-match docker-validate docker-eval docker-baseline docker-eval-2b

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

# Milestone 2C: do picked-up items respawn under the match's host arguments (with a negative control)?
bench-items:
	for m in MAP10 E1M5; do $(PYTHON) -m engine.bench.item_respawn --map $$m --out out/bench || exit 1; done

match:
	$(PYTHON) -m engine.runner --duration-tics $(TICS) --seed $(SEED) --out bundles

validate:
	$(PYTHON) -m engine.validate_bundle $(BUNDLE)

# Milestone 2A evaluation: five-minute matches on MAP01 at fixed seeds, then the signature report.
eval:
	for s in $(SEEDS); do $(PYTHON) -m engine.runner --duration-tics 10500 --seed $$s --personalities $(PERSONALITIES) --out $(EVAL_OUT) || exit 1; done
	$(PYTHON) tools/signatures.py --bots $(BOTS) --json $(EVAL_OUT)/signatures.json $(EVAL_OUT)/2*

# Separation baseline: every slot a placeholder, five-minute matches per map; then frags per minute per map.
# A capture that fails its checks is run once more (the host player-name fault, docs/MATCH_FORMAT.md section 9).
baseline:
	for m in $(BASE_MAPS); do for s in $(BASE_SEEDS); do \
		$(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities none --out $(BASE_OUT)/$$m \
		|| $(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities none --out $(BASE_OUT)/$$m || exit 1; \
	done; done
	$(PYTHON) tools/frag_rate.py --json $(BASE_OUT)/frag_rate.json $(BASE_OUT)/*/2*

# All eight personalities; signatures against the baseline for each map (docs/SIGNATURES.md, separation).
eval-2b:
	for m in $(EVAL2B_MAPS); do for s in $(EVAL2B_SEEDS); do \
		$(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all --out $(EVAL2B_OUT)/$$m \
		|| $(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all --out $(EVAL2B_OUT)/$$m || exit 1; \
	done; done
	$(MAKE) signatures-2b

signatures-2b:
	$(PYTHON) tools/frag_rate.py --json $(EVAL2B_OUT)/frag_rate.json $(EVAL2B_OUT)/*/2*
	$(PYTHON) tools/signatures.py --baseline $(BASE_OUT)/*/2* --json $(EVAL2B_OUT)/signatures.json $(EVAL2B_OUT)/*/2*

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
	$(DOCKER_RUN) make eval SEEDS="$(SEEDS)" EVAL_OUT=$(EVAL_OUT) PERSONALITIES=$(PERSONALITIES) BOTS=$(BOTS)

docker-baseline:
	$(DOCKER_RUN) make baseline BASE_MAPS="$(BASE_MAPS)" BASE_SEEDS="$(BASE_SEEDS)" BASE_OUT=$(BASE_OUT)

docker-eval-2b:
	$(DOCKER_RUN) make eval-2b EVAL2B_MAPS="$(EVAL2B_MAPS)" EVAL2B_SEEDS="$(EVAL2B_SEEDS)" EVAL2B_OUT=$(EVAL2B_OUT) BASE_OUT=$(BASE_OUT)
