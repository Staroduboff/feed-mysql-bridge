#!/bin/bash
# Лестница нагрузки на Centrifugo витрины sport-live. Запускается на машине-генераторе.
#   ladder.sh <режим> <ступени через запятую> <секунд на ступень> [каналов событий] [процессов]
# Пример: ladder.sh narrow 200,500,1000,2000,4000 120
#         ladder.sh wide 50,100,200,400 180 120 4
#
# Процессов (shards) — на сколько процессов Node дробится ступень. JavaScript однопоточен:
# один процесс с тысячами сокетов упирается в ядро и начинает сам добавлять задержку в
# измеряемую доставку. Держите CPU каждого процесса ниже ~70 % (он печатается в статистике).
#
# Сторож (wsguard.py) поднимается на всё время лестницы и обрывает генератор, если нагрузка
# начинает мешать основной работе этого сервера (сеть, отклик mysqld, load, память).
set -u
MODE=${1:-narrow}
STEPS=${2:-200,500,1000}
HOLD=${3:-120}
EVENTS=${4:-120}
SHARDS=${5:-1}
DIR=/tmp/wsload
mkdir -p $DIR
STAMP=$(date +%Y%m%d-%H%M)
RUN=$DIR/ladder-$MODE-$STAMP
mkdir -p $RUN

TOTAL=0
IFS=',' read -ra ARR <<< "$STEPS"
for n in "${ARR[@]}"; do TOTAL=$((TOTAL + HOLD + n / 100 + 20)); done
echo "лестница: режим $MODE, ступени $STEPS, полка $HOLD с, ориентировочно $((TOTAL / 60)) мин"
echo "каталог прогона: $RUN"

ulimit -n 200000 2>/dev/null || ulimit -n 65536
python3 /opt/wsload/wsguard.py $((TOTAL + 120)) > $RUN/guard.log 2>&1 &
GUARD=$!
trap 'kill $GUARD 2>/dev/null; pkill -TERM -f wsload.js 2>/dev/null' EXIT

for n in "${ARR[@]}"; do
  echo
  echo "=== ступень $n клиентов ($(date +%H:%M:%S)) ==="
  per=$(( n / SHARDS )); ramp=$(( 100 / SHARDS )); [ $ramp -lt 5 ] && ramp=5
  pids=()
  for sh in $(seq 1 $SHARDS); do
    node /opt/wsload/wsload.js --clients "$per" --mode "$MODE" --events "$EVENTS" \
         --ramp "$ramp" --duration "$HOLD" --label "$MODE-$n-sh$sh" \
         --out "$RUN/clients.jsonl" > "$RUN/step-$n-sh$sh.log" 2>&1 &
    pids+=($!)
  done
  wait "${pids[@]}"
  cat "$RUN"/step-$n-sh*.log > "$RUN/step-$n.log"
  echo "  процессов $SHARDS по $per клиентов; CPU процессов: $(grep -h "CPU процесса-генератора" "$RUN"/step-$n-sh*.log | grep -o "[0-9]* %" | tr "\n" " ")"
  grep -h -E "длительность|сообщений|разброс" "$RUN/step-$n.log" | tail -6
  if grep -q "ПРЕДОХРАНИТЕЛЬ" "$RUN/step-$n.log" || grep -q "СТОП" $RUN/guard.log 2>/dev/null; then
    echo "останов на ступени $n — предохранитель или сторож"
    break
  fi
  sleep 15   # пауза между ступенями: сервер возвращается в покой
done
kill $GUARD 2>/dev/null
echo
echo "лестница завершена, данные в $RUN"
