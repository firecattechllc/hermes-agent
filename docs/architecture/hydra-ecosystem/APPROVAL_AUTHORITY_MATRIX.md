# Approval Authority Matrix

| Action | Hermes | Prime | User |
|---|---:|---:|---:|
| Inspect source | Yes | No | Oversees |
| Create branch | Yes | No | Oversees |
| Run tests | Yes | No | Oversees |
| Open draft PR | Yes | No | Oversees |
| Merge release PR | Propose only | No | Required |
| Sign release | Prepare only | May verify identity | Required |
| Deploy to Titan | Prepare only | Device authorization | Required |
| Register device | Propose only | Enforces | Required |
| Revoke device | Propose only | Enforces | Required |
| Install secrets | No plaintext handling | May authorize device | Required |
| Enable brokerage read access | Propose only | May gate device | Required |
| Enable live trading | Never autonomously | May enforce identity | Required |
| Approve spending | Recommend only | No | Required |
| Destructive recovery | Prepare only | May gate device | Required |
