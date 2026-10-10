.PHONY: sync format check check-format typecheck lint lint-report test

UV ?= uv
RUN = $(UV) run --frozen
OPERATOR_MODULES = $(wildcard operators/*.py)
# Enforce maintainability and correctness, not an arbitrary aggregate score.
LINT_CHECKS = E,missing-module-docstring,missing-class-docstring,missing-function-docstring,invalid-name,unused-import,unused-variable,redefined-outer-name,too-many-branches,too-many-statements,too-many-boolean-expressions,too-many-locals,line-too-long,unspecified-encoding,subprocess-run-check

sync:
	$(UV) sync --locked

format:
	$(RUN) yapf -i -r operators

check: check-format typecheck lint test

check-format:
	$(RUN) yapf --quiet -r operators

typecheck:
	$(RUN) ty check operators

lint:
	$(RUN) pylint --persistent=n --score=no --disable=all --enable=$(LINT_CHECKS) $(OPERATOR_MODULES)

# Show every Pylint finding separately; this is not a claim of full lint compliance.
lint-report:
	$(RUN) pylint --persistent=n --score=no $(OPERATOR_MODULES)

test:
	$(RUN) python -m unittest discover -s operators/tests
