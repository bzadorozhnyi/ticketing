# syntax=docker/dockerfile:1

FROM rust:1.99.1-slim-trixie AS builder

WORKDIR /app

COPY . .

RUN cargo build --release --locked


FROM debian:trixie-slim AS runtime

COPY --from=builder /app/target/release/benchmark-stub /usr/local/bin/benchmark-stub

EXPOSE 8000

CMD ["benchmark-stub"]
