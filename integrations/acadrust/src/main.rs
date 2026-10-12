//! Read-only DWG adapter. Output is a derived DXF, never the source drawing.
use acadrust::{DwgReader, DxfWriter};
use std::{env, fs, path::PathBuf};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        return Err("usage: extractor-dwg INPUT.dwg OUTPUT.dxf REPORT.json".into());
    }
    let input = fs::canonicalize(&args[1])?;
    let output = PathBuf::from(&args[2]);
    if output.exists() && fs::canonicalize(&output)? == input {
        return Err("refusing to overwrite source".into());
    }
    let size = fs::metadata(&input)?.len();
    if !(1024..=268_435_456).contains(&size) {
        return Err("DWG outside supported 1 KiB–256 MiB input range".into());
    }
    let outcome = DwgReader::from_file(&input)?.read_with_stats()?;
    fs::write(&args[3], serde_json::to_vec_pretty(&outcome.stats)?)?;
    if !outcome.stats.stream_completed || outcome.stats.output_entities == 0 {
        return Err("reader did not complete or returned no entities; inspect report".into());
    }
    DxfWriter::new(&outcome.document).write_to_file(&output)?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}
