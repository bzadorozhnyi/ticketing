# Benchmarks

Compare the Rust services with the same code in Python. Results and conclusions: `docs/adr/0002-rust-for-the-gateway.md` and `docs/adr/0004-rust-for-seat-hold.md`.

## Gateway

- `python-proxy/`: the baseline. It must keep doing the same work as the gateway, or the numbers mean nothing.
- `stub/`: a fast upstream with a 795-byte body and an optional delay.
- `run.py`: builds the images, starts containers and runs `oha` inside the Docker network.

Needs Docker with 10 CPUs. Scenarios with `--stack` also need the compose stack with demo data.
Run from this directory; each load scenario takes 7-10 minutes:

```
python3 run.py parity                                             # both proxies behave the same; run this first
python3 run.py load --cpus 1                                      # 1. maximum load
python3 run.py load --cpus 2
python3 run.py load --cpus 1 --rate 1970                          # 2. fixed rate, half of the Python maximum
python3 run.py load --cpus 1 --connections 1000 --delay-ms 100    # 3. spike
python3 run.py load --cpus 1 --stack --connections 16             # 4. through real catalog
python3 run.py load --cpus 1 --stack --connections 16 --rate 100
python3 run.py footprint                                          # image size, idle memory, start time
```

## Seat-hold

- `python-seat-hold/`: the baseline, with the same SQL and rules.
- `run_seat_hold.py`: starts its own Postgres and runs `oha` inside the Docker network.

`oha -Z` picks lines at random with repeats, so `--scenario free` uses a TTL of 1 ms: every request overwrites an expired hold.

```
python3 run_seat_hold.py parity                                          # both services behave the same; run this first
python3 run_seat_hold.py load --scenario free                            # 1. write path
python3 run_seat_hold.py load --scenario hot                             # 2. one held seat
python3 run_seat_hold.py load --scenario free --rate 1600                # 3. fixed rate, half of the Python maximum
python3 run_seat_hold.py load --scenario hot --connections 1000          # 4. spike
python3 run_seat_hold.py load --scenario hot --cpus 4 --runs 3 --seconds 15
python3 run_seat_hold.py footprint
```
