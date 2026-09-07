## Einsatz im CI-Runner

Der Begriff **CI-Runner** bezeichnet in diesem Projekt lediglich das Host-System, auf dem das Skript ausgeführt wird.

Der tatsächliche Hostname ist frei wählbar und muss **nicht** `ci-runner` lauten. Beispiele:

```text
ci-runner
build-runner
gitea-runner
runner01
notebook-ci
```

Entscheidend ist nur, dass das System die technischen Voraussetzungen für Docker und Kata Containers erfüllt.

Kata Containers eignet sich besonders für CI-Jobs, bei denen eine stärkere Isolation als bei klassischen Docker-Containern gewünscht ist.

Beispiel:

```bash
docker run \
  --runtime kata \
  --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  ubuntu:24.04 \
  bash -c "./build.sh"
```
