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

# Milestone 2D holdout: thresholds fitted on eval-2c, spots scored by the traffic of the earlier evaluations.
EVAL2D_SEEDS ?= 501 502 503 504 505
EVAL2D_OUT ?= bundles/eval-2d
TRAFFIC_EVALS ?= bundles/eval-2c

# Milestone 2C: two placeholder matches per map seed the traffic map spots are scored by, then the evaluation.
EVAL2C_MAPS ?= MAP10 E1M5
TRAFFIC_SEEDS ?= 211 212
TRAFFIC_OUT ?= bundles/traffic-2c
EVAL2C_SEEDS ?= 401 402 403 404 405
EVAL2C_OUT ?= bundles/eval-2c
MIN_FREE_GB ?= 20
# Refuse to start a match with less than MIN_FREE_GB free where bundles are written.
DISK_CHECK = avail=$$(df -Pk . | awk 'NR==2 {print int($$4 / 1048576)}'); \
	if [ "$$avail" -lt $(MIN_FREE_GB) ]; then echo "refusing to start: $$avail GB free, under $(MIN_FREE_GB) GB"; exit 1; fi; \
	echo "disk: $$avail GB free"

.PHONY: test bench bench-smoke bench-controlled bench-personality bench-spawn bench-items match validate eval baseline eval-2b signatures-2b traffic-2c eval-2c signatures-2c eval-2d fit-2d signatures image docker-test docker-bench docker-match docker-validate docker-eval docker-baseline docker-eval-2b docker-traffic-2c docker-eval-2c docker-signatures-2c docker-eval-2d docker-fit-2d

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
	@echo "2B gated against the placeholder baseline (tools/signatures.py --baseline), which milestone 2C removed; use signatures-2c"

# Placeholder matches whose positions make each map's traffic map (engine/personality/traffic.py).
traffic-2c:
	for m in $(EVAL2C_MAPS); do for s in $(TRAFFIC_SEEDS); do \
		$(DISK_CHECK); \
		$(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities none --out $(TRAFFIC_OUT)/$$m \
		|| $(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities none --out $(TRAFFIC_OUT)/$$m || exit 1; \
	done; done

# All eight personalities, spots scored by the traffic map of TRAFFIC_OUT; signatures against the roster.
eval-2c:
	for m in $(EVAL2C_MAPS); do for s in $(EVAL2C_SEEDS); do \
		$(DISK_CHECK); \
		$(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all --traffic $(TRAFFIC_OUT)/$$m --out $(EVAL2C_OUT)/$$m \
		|| $(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all --traffic $(TRAFFIC_OUT)/$$m --out $(EVAL2C_OUT)/$$m || exit 1; \
	done; done
	$(MAKE) signatures-2c

# Traffic from every earlier evaluation of the map (TRAFFIC_EVALS), placeholder bundles only as fallback.
eval-2d:
	for m in $(EVAL2C_MAPS); do for s in $(EVAL2D_SEEDS); do \
		$(DISK_CHECK); \
		$(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all \
			--traffic $(foreach d,$(TRAFFIC_EVALS),$(d)/$$m) $(TRAFFIC_OUT)/$$m --out $(EVAL2D_OUT)/$$m \
		|| $(PYTHON) -m engine.runner --map $$m --duration-tics 10500 --seed $$s --personalities all \
			--traffic $(foreach d,$(TRAFFIC_EVALS),$(d)/$$m) $(TRAFFIC_OUT)/$$m --out $(EVAL2D_OUT)/$$m || exit 1; \
	done; done
	$(MAKE) signatures-2c EVAL2C_OUT=$(EVAL2D_OUT)

signatures-2c:
	$(PYTHON) tools/frag_rate.py --json $(EVAL2C_OUT)/frag_rate.json $(EVAL2C_OUT)/*/2*
	$(PYTHON) tools/signatures.py --json $(EVAL2C_OUT)/signatures.json $(EVAL2C_OUT)/*/2*
	$(PYTHON) tools/hold_contact.py --bots NUL,HEX --json $(EVAL2C_OUT)/hold_contact.json $(EVAL2C_OUT)/*/2*
	$(PYTHON) tools/item_latency.py --bots KRN --json $(EVAL2C_OUT)/item_latency.json $(EVAL2C_OUT)/*/2*

# Milestone 2D: fit every gating threshold to FIT_ON against the roster; --write applies separating fits to the sheets.
FIT_ON ?= bundles/eval-2c
fit-2d:
	$(PYTHON) tools/fit_thresholds.py --json $(FIT_ON)/fit-2d.json --run fit-2d --source $(notdir $(FIT_ON)) $(FIT_ARGS) $(FIT_ON)/*/2*

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

docker-traffic-2c:
	$(DOCKER_RUN) make traffic-2c EVAL2C_MAPS="$(EVAL2C_MAPS)" TRAFFIC_SEEDS="$(TRAFFIC_SEEDS)" TRAFFIC_OUT=$(TRAFFIC_OUT)

docker-eval-2c:
	$(DOCKER_RUN) make eval-2c EVAL2C_MAPS="$(EVAL2C_MAPS)" EVAL2C_SEEDS="$(EVAL2C_SEEDS)" EVAL2C_OUT=$(EVAL2C_OUT) TRAFFIC_OUT=$(TRAFFIC_OUT)

docker-signatures-2c:
	$(DOCKER_RUN) make signatures-2c EVAL2C_OUT=$(EVAL2C_OUT)

docker-eval-2d:
	$(DOCKER_RUN) make eval-2d EVAL2C_MAPS="$(EVAL2C_MAPS)" EVAL2D_SEEDS="$(EVAL2D_SEEDS)" EVAL2D_OUT=$(EVAL2D_OUT) TRAFFIC_EVALS="$(TRAFFIC_EVALS)" TRAFFIC_OUT=$(TRAFFIC_OUT)

docker-fit-2d:
	$(DOCKER_RUN) make fit-2d FIT_ON=$(FIT_ON) FIT_ARGS="$(FIT_ARGS)"

docker-eval-2b:
	$(DOCKER_RUN) make eval-2b EVAL2B_MAPS="$(EVAL2B_MAPS)" EVAL2B_SEEDS="$(EVAL2B_SEEDS)" EVAL2B_OUT=$(EVAL2B_OUT) BASE_OUT=$(BASE_OUT)
