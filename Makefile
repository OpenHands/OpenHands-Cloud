PROJECTDIR := $(shell pwd)

# Discover Helm charts that have a corresponding Replicated HelmChart manifest.
# Charts without a manifest (e.g. subcharts only used as dependencies) are
# excluded from the Replicated release to avoid lint errors.
CHARTDIR    := $(PROJECTDIR)/charts
MANIFESTDIR := $(PROJECTDIR)/replicated
CHARTS      := $(shell for f in $(MANIFESTDIR)/*.yaml $(MANIFESTDIR)/*.yml; do \
                  name=$$(yq '.spec.chart.name // ""' "$$f" 2>/dev/null); \
                  [ -n "$$name" ] && [ -d $(CHARTDIR)/$$name ] && echo $$name; \
                done | sort -u)

# Discover all Replicated manifest files
MANIFESTS   := $(shell find $(MANIFESTDIR) -name '*.yaml' -o -name '*.yml')

# All Chart.yaml files — used as dependencies so manifest builds re-run when any chart version changes
CHART_YAMLS := $(shell find $(CHARTDIR) -name 'Chart.yaml')

# Release metadata: version comes from the openhands chart, channel from the current git branch
VERSION     ?= $(shell yq .version $(CHARTDIR)/openhands/Chart.yaml)
REPLICATED_APP ?= openhands
CHANNEL     := $(shell git branch --show-current)
ifeq ($(CHANNEL), main)
	CHANNEL=Unstable
endif

BUILDDIR      := $(PROJECTDIR)/build
RELEASE_FILES :=

# ── Manifest targets ────────────────────────────────────────────────
# For each replicated manifest, generate a build rule that:
#   1. Copies the source YAML into build/
#   2. If the manifest references a chart (spec.chart.name), injects the
#      matching chartVersion from charts/<name>/Chart.yaml
#
# This means replicated manifests don't need to hardcode chart versions —
# they're always pulled from the chart source of truth at build time.
#
# Note on escaping: this macro is expanded via $(eval $(call ...)), which
# double-expands variables. Shell variable references need $$$$ to survive
# both passes and arrive as $VAR in the shell. Make variables use $$ to
# defer expansion to recipe execution time.
define make-manifest-target
$(BUILDDIR)/$(notdir $1): $1 $(CHART_YAMLS) | $$(BUILDDIR)
	cp $1 $$(BUILDDIR)/$$(notdir $1)
	@CHART_NAME=$$$$(yq '.spec.chart.name // ""' $$(BUILDDIR)/$$(notdir $1)); \
	if [ -n "$$$$CHART_NAME" ] && [ -f $(CHARTDIR)/$$$$CHART_NAME/Chart.yaml ]; then \
		CHART_VER=$$$$(yq .version $(CHARTDIR)/$$$$CHART_NAME/Chart.yaml); \
		yq -i ".spec.chart.chartVersion = \"$$$$CHART_VER\"" $$(BUILDDIR)/$$(notdir $1); \
		echo "Updated $$(notdir $1) chartVersion to $$$$CHART_VER"; \
	fi
RELEASE_FILES := $(RELEASE_FILES) $(BUILDDIR)/$(notdir $1)
manifests:: $(BUILDDIR)/$(notdir $1)
endef
$(foreach element,$(MANIFESTS),$(eval $(call make-manifest-target,$(element))))

# ── Chart targets ───────────────────────────────────────────────────
# For each Helm chart, package it into a versioned .tgz in build/.
# Dependencies include all yaml/tpl/schema files so changes trigger a rebuild.
define make-chart-target
$(eval VER := $(shell yq .version $(CHARTDIR)/$1/Chart.yaml))
$(BUILDDIR)/$1-$(VER).tgz : $(CHARTDIR)/$1 $(shell find $(CHARTDIR)/$1 -name '*.yaml' -o -name '*.yml' -o -name "*.tpl" -o -name "NOTES.txt" -o -name "values.schema.json") | $$(BUILDDIR)
	@# Rewrite any dependency that points to a remote registry but exists as a
	@# sibling chart to use a local file:// reference instead. This lets
	@# `helm package -u` resolve unpublished chart versions during local builds.
	@cp $(CHARTDIR)/$1/Chart.yaml $(CHARTDIR)/$1/Chart.yaml.bak
	@trap 'mv $(CHARTDIR)/$1/Chart.yaml.bak $(CHARTDIR)/$1/Chart.yaml' EXIT; \
	for dep in $$$$(yq -r '.dependencies[].name // ""' $(CHARTDIR)/$1/Chart.yaml); do \
		if [ -d $(CHARTDIR)/$$$$dep ]; then \
			yq -i "(.dependencies[] | select(.name == \"$$$$dep\")).repository = \"file://../$$$$dep\"" $(CHARTDIR)/$1/Chart.yaml; \
		fi; \
	done; \
	helm package -u $(CHARTDIR)/$1 -d $(BUILDDIR)/
RELEASE_FILES := $(RELEASE_FILES) $(BUILDDIR)/$1-$(VER).tgz
charts:: $(BUILDDIR)/$1-$(VER).tgz
endef
$(foreach element,$(CHARTS),$(eval $(call make-chart-target,$(element))))

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# ── Phony targets ───────────────────────────────────────────────────

# Remove the build directory. Runs before lint/release to prevent stale
# chart tarballs (from previous versions) from conflicting with current ones.
.PHONY: clean
clean:
	rm -rf $(BUILDDIR)

# Validate all built manifests and charts with the Replicated linter
.PHONY: lint
lint: clean $(RELEASE_FILES)
	replicated release lint --yaml-dir $(BUILDDIR)

# Build everything, lint, then publish a release to the Replicated channel
.PHONY: release
release: clean $(RELEASE_FILES) lint
	replicated release create \
	 	--app $(REPLICATED_APP) \
		--version $(VERSION) \
		--yaml-dir $(BUILDDIR) \
		--ensure-channel \
		--promote $(CHANNEL)
