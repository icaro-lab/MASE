#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"

if [[ -z "${AGENT_WORKER_IMAGE:-}" ]] || [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
  eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"
fi

docker build -t "$AGENT_WORKER_IMAGE" -f services/agent-launcher/Dockerfile --target runtime .

build_targets="$(
  REPO_ROOT="$repo_root" COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" ruby <<'RUBY'
require "yaml"
require "pathname"

repo_root = Pathname.new(ENV.fetch("REPO_ROOT"))
namespace = ENV.fetch("COMPOSE_PROJECT_NAME").strip
namespace = "mase" if namespace.empty?

Dir.glob(repo_root.join("environments", "*", "environment.yaml")).sort.each do |manifest_path|
  payload = YAML.load_file(manifest_path) || {}
  next unless payload.is_a?(Hash)

  manifest_dir = Pathname.new(manifest_path).dirname
  next if manifest_dir.basename.to_s.start_with?(".", "_")
  environment_id = (payload["id"] || manifest_dir.basename.to_s).to_s.strip
  next if environment_id.empty?

  launch = payload["launch"]
  next unless launch.is_a?(Hash)
  images = launch["images"]
  next unless images.is_a?(Hash)

  %w[backend frontend].each do |asset_kind|
    image_spec = images[asset_kind]
    next unless image_spec.is_a?(Hash)

    repository = image_spec["repository"].to_s.strip
    dockerfile = image_spec["dockerfile"].to_s.strip
    context_path = image_spec["context"].to_s.strip
    next if repository.empty? || dockerfile.empty? || context_path.empty?

    puts [environment_id, asset_kind, "#{repository}:#{namespace}", dockerfile, context_path].join("\t")
  end
end
RUBY
)"

while IFS=$'\t' read -r environment_id asset_kind image_ref dockerfile context_path; do
  if [[ -z "${environment_id:-}" ]] || [[ -z "${image_ref:-}" ]] || [[ -z "${dockerfile:-}" ]] || [[ -z "${context_path:-}" ]]; then
    continue
  fi
  docker build -t "$image_ref" -f "$dockerfile" "$context_path"
done <<< "$build_targets"
