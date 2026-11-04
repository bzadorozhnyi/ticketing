# syntax=docker/dockerfile:1

FROM rust:1.99.1-slim-trixie AS chef

RUN cargo install cargo-chef --version 0.1.78 --locked

WORKDIR /app


FROM chef AS planner

COPY . .

RUN cargo chef prepare --recipe-path recipe.json


FROM chef AS builder

COPY --from=planner /app/recipe.json recipe.json

RUN cargo chef cook --release --locked --recipe-path recipe.json

COPY . .

RUN cargo build --release --locked --bins


FROM debian:trixie-slim AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app app

COPY --from=builder /app/target/release/gateway /app/target/release/issue-token /usr/local/bin/

USER app

EXPOSE 8000

CMD ["gateway"]
