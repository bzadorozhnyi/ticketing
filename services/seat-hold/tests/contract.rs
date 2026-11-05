use std::fs;

use seat_hold::docs::{contract_path, render_contract};

#[test]
fn the_contract_file_matches_the_code() {
    let stored = fs::read_to_string(contract_path()).unwrap();

    assert_eq!(
        stored,
        render_contract().unwrap(),
        "run: cargo run --bin export-openapi"
    );
}
