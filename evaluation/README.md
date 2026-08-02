# AIOps automated evaluation

Configure the existing SmartLife fault-controller start and stop URLs in
`evaluation_config.yaml`, or provide environment variables such as
`EVAL_CPU_HIGH_START_URL` and `EVAL_CPU_HIGH_STOP_URL`.

Run the default 18 trials:

```powershell
.\.venv\Scripts\python.exe -m evaluation.runner
```

Validate dependencies and output shape without injecting faults:

```powershell
.\.venv\Scripts\python.exe -m evaluation.runner --dry-run --repetitions 1
```

The runner always attempts the stop API in `finally`, waits for the exact
`alert_event.id` to become resolved, and writes `evaluation_result.json` and
`evaluation_result.csv`.
