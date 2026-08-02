fn main() {
    if let Ok(target) = std::env::var("TARGET") {
        println!("cargo:rustc-env=DEEPCODE_TARGET_TRIPLE={target}");
    }
    tauri_build::build()
}
