index=main status=failed
| stats count by action

index=main status=failed
| head 20

index=main process="mimikatz.exe"
| table _time host process parent_process user severity

index=main action=process_create
| search process="*powershell*"
| table _time host process severity