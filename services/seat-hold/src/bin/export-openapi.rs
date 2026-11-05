use std::error::Error;
use std::fs;

use seat_hold::docs::{contract_path, render_contract};

fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    fs::write(contract_path(), render_contract()?)?;
    Ok(())
}
