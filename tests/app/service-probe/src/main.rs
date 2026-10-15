// SPDX-FileCopyrightText: 2026 roolrz
// SPDX-License-Identifier: Apache-2.0

//! Native lifecycle/stdio probe. The plain mode deliberately uses only std I/O.
use std::{io::Read, time::Duration};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    match std::env::args().nth(1).as_deref() {
        Some("plain") => {
            let mut byte = [0];
            if std::io::stdin().read(&mut byte)? != 0 {
                return Err("service stdin is not EOF".into());
            }
            println!("SERVICE_STDOUT_OK");
            eprintln!("SERVICE_STDERR_OK");
        }
        Some("fail") => {
            eprintln!("SERVICE_FAILURE_ATTEMPT");
            return Err("intentional failure".into());
        }
        Some("memory-lock") => {
            let startup = hyper_rt::process::startup()?;
            let root = startup.borrow(hyper_os::startup::ROOT_VMAR)?;
            let result = hyper_os::memory::lock_current_memory(root);
            match std::env::args().nth(2).as_deref() {
                Some("allowed") => {
                    result?;
                    hyper_os::memory::unlock_current_memory(root)?;
                    println!("COMPONENT_MEMORY_LOCK_ALLOWED_OK");
                }
                Some("denied") => {
                    if !matches!(
                        result,
                        Err(hyper_os::Error::Status(hyper_os::Status::ACCESS_DENIED))
                    ) {
                        return Err("resident=false did not deny memory locking".into());
                    }
                    println!("COMPONENT_MEMORY_LOCK_DENIED_OK");
                }
                _ => return Err("expected memory-lock allowed or denied".into()),
            }
        }
        Some("echo") => {
            let mut startup = hyper_rt::process::startup()?;
            let outgoing = hyper_os::capability_channel::CapabilityChannel::from_handle(
                startup.take(hyper_component::OUTGOING)?,
            );
            hyper_component::connection::publish(
                startup.borrow(hyper_component::CONNECTOR)?,
                "echo",
            )?;
            println!("COMPONENT_ECHO_PUBLISHED");
            let active = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
            loop {
                let (name, session) =
                    hyper_component::connection::accept(&outgoing, hyper_os::DEADLINE_INFINITE)?;
                if name != "echo" || active.load(std::sync::atomic::Ordering::Acquire) >= 8 {
                    continue;
                }
                active.fetch_add(1, std::sync::atomic::Ordering::AcqRel);
                let active = active.clone();
                std::thread::spawn(move || {
                    if let Ok(call) =
                        hyper_service_ipc::receive::<String>(&session, hyper_os::DEADLINE_INFINITE)
                    {
                        let reply = call.request.clone();
                        let _ = call.respond(&reply);
                    }
                    active.fetch_sub(1, std::sync::atomic::Ordering::AcqRel);
                });
            }
        }
        Some("connect") => {
            let startup = hyper_rt::process::startup()?;
            let barrier = std::sync::Arc::new(std::sync::Barrier::new(8));
            let mut workers = Vec::new();
            for n in 0..8 {
                let connector = startup
                    .borrow(hyper_component::CONNECTOR)?
                    .duplicate(hyper_service_ipc::CLIENT_RIGHTS)?;
                let barrier = barrier.clone();
                workers.push(std::thread::spawn(move || -> hyper_os::Result<()> {
                    barrier.wait();
                    let session = hyper_component::connection::connect(
                        connector.as_handle_ref(),
                        "echo",
                        hyper_service_ipc::deadline()?,
                    )?;
                    let value = format!("connection-{n}");
                    let reply: String =
                        hyper_service_ipc::request(session.as_handle_ref(), &value)?;
                    if reply != value {
                        return Err(hyper_os::Error::InvalidResponse);
                    }
                    Ok(())
                }));
            }
            for worker in workers {
                worker.join().map_err(|_| "client thread failed")??;
            }
            if hyper_component::connection::connect(
                startup.borrow(hyper_component::CONNECTOR)?,
                "secret",
                hyper_service_ipc::deadline()?,
            )
            .is_ok()
            {
                return Err("undeclared capability was delivered".into());
            }
            println!("COMPONENT_CONCURRENT_CONNECT_OK");
            println!("COMPONENT_UNDECLARED_DENIED_OK");
        }
        Some("idle") => loop {
            std::thread::sleep(Duration::from_secs(1));
        },
        Some("timeout") => {
            let startup = hyper_rt::process::startup()?;
            let until = hyper_os::time::deadline_after(Duration::from_millis(500))?.as_raw();
            if hyper_component::connection::connect(
                startup.borrow(hyper_component::CONNECTOR)?,
                "echo",
                until,
            )
            .is_ok()
            {
                return Err("unpublished protocol was connected".into());
            }
            println!("COMPONENT_DEADLINE_OK");
        }
        Some("observe") => {
            let startup = hyper_rt::process::startup()?;
            let reply: hyper_component::Reply = hyper_service_ipc::request(
                startup.borrow(hyper_component::OBSERVE)?,
                &hyper_component::Request::Stop {
                    path: "/session-manager".into(),
                },
            )?;
            if reply.error.is_none() {
                return Err("read-only observer accepted a stop request".into());
            }
            println!("SERVICE_OBSERVER_DENIED_OK");
        }
        Some("ipc") => {
            let (listener, client) = hyper_os::capability_channel::CapabilityChannel::create()?;
            let worker = std::thread::spawn(move || -> hyper_os::Result<()> {
                let call = hyper_service_ipc::receive::<String>(
                    &listener,
                    hyper_service_ipc::deadline()?,
                )?;
                if call.request != "large reply" {
                    return Err(hyper_os::Error::InvalidResponse);
                }
                call.respond(&"x".repeat(96 * 1024))
            });
            let reply: String = hyper_service_ipc::request(client.as_handle_ref(), &"large reply")?;
            worker.join().map_err(|_| "IPC worker terminated")??;
            if reply.len() != 96 * 1024 || !reply.bytes().all(|b| b == b'x') {
                return Err("large reply framing lost bytes".into());
            }
            println!("SERVICE_LARGE_REPLY_OK");
        }
        _ => {
            return Err(
                "expected plain, fail, memory-lock, echo, connect, idle, timeout, observe, or ipc"
                    .into(),
            );
        }
    }
    Ok(())
}
fn main() -> std::process::ExitCode {
    match run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("service-probe: {e}");
            std::process::ExitCode::FAILURE
        }
    }
}
