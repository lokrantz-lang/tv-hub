#!/bin/sh
# Testar appens DLNA-klient (Dlna.kt) mot tests/fake_dlna.py. Kräver kotlinc eller Gradles Kotlin-kompilator.
set -e
cd "$(dirname "$0")"
OUT=$(mktemp -d)
L=${KOTLIN_LIB:-/opt/gradle-8.14.3/lib}
CP="$L/kotlin-compiler-embeddable-2.0.21.jar:$L/kotlin-stdlib-2.0.21.jar:$L/kotlin-script-runtime-2.0.21.jar:$L/kotlin-reflect-2.0.21.jar:$L/kotlin-daemon-embeddable-2.0.21.jar:$L/trove4j-1.0.20200330.jar:$L/annotations-24.0.1.jar:$L/kotlinx-coroutines-core-jvm-1.6.4.jar"
java -cp "$CP" org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -no-stdlib -cp "$L/kotlin-stdlib-2.0.21.jar" -d "$OUT" -jvm-target 17 \
  ../../android/app/src/main/java/se/tvhub/box/Dlna.kt ../../android/app/src/main/java/se/tvhub/box/MediaNames.kt DlnaTest.kt 2>&1 | grep -v "^warning" || true
python3 ../fake_dlna.py 28301 28302 & PID=$!
sleep 1
java -cp "$OUT:$L/kotlin-stdlib-2.0.21.jar" DlnaTestKt 28301 28302; RC=$?
kill $PID 2>/dev/null || true
exit $RC
