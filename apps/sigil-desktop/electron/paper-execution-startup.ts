export type PaperExecutionStartupStatus = Readonly<{
  environment: string
  live_execution: boolean
  broker: string
  broker_base_url: string
  broker_submission: boolean
  activated: boolean
  paused: boolean
  kill_switch: boolean
}>

type BridgeRequest = Readonly<{
  command: string
}>

type BridgeResponse =
  | {
      ok: true
      result: PaperExecutionStartupStatus
    }
  | {
      ok: false
      error: string
      message: string
    }

type BridgeRunner = (request: BridgeRequest) => Promise<BridgeResponse>

const ALPACA_PAPER_BASE_URL = 'https://paper-api.alpaca.markets'

function isPaperOnly(status: PaperExecutionStartupStatus): boolean {
  return (
    status.environment === 'paper' &&
    status.live_execution === false &&
    status.broker === 'alpaca_paper' &&
    status.broker_base_url === ALPACA_PAPER_BASE_URL
  )
}

function isActive(status: PaperExecutionStartupStatus): boolean {
  return (
    status.activated &&
    !status.paused &&
    !status.kill_switch &&
    status.broker_submission
  )
}

function hasPersistedAuthority(status: PaperExecutionStartupStatus): boolean {
  return status.activated || status.broker_submission || !status.kill_switch
}

export async function enableGovernedPaperExecutionByDefault(
  runBridgeRequest: BridgeRunner
): Promise<BridgeResponse> {
  const current = await runBridgeRequest({ command: 'paper_execution_status' })

  if (!current.ok || !isPaperOnly(current.result)) {
    return current.ok
      ? {
          ok: false,
          error: 'unsafe_paper_execution_identity',
          message:
            'Governed paper execution did not report the required paper-only identity.'
        }
      : current
  }

  const activated = await runBridgeRequest({
    command: 'paper_execution_activate'
  })

  if (!activated.ok) {
    if (hasPersistedAuthority(current.result)) {
      await runBridgeRequest({ command: 'paper_execution_deactivate' })
    }

    return activated
  }

  if (!isPaperOnly(activated.result) || !isActive(activated.result)) {
    await runBridgeRequest({ command: 'paper_execution_deactivate' })

    return {
      ok: false,
      error: 'paper_execution_startup_not_active',
      message:
        'Governed paper execution did not become active within the paper-only boundary.'
    }
  }

  return activated
}
