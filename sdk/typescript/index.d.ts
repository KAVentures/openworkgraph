export type ObservationLevel = 'native_trace' | 'instrumented_tools' | 'mcp_only' | 'os_observed' | 'outcome_only';
export type ToolCategory = 'filesystem' | 'shell' | 'browser' | 'code' | 'search' | 'network' | 'database' | 'messaging' | 'issue_tracker' | 'deployment' | 'mcp' | 'other' | 'none';
export type TokenUsage = Partial<Record<'input_tokens' | 'output_tokens' | 'cached_input_tokens' | 'total_tokens', number>>;

export interface ObserverOptions {
  provider?: string;
  framework?: string;
  observationLevel?: ObservationLevel;
  endpoint?: string;
  token?: string;
  deviceId?: string;
  sensorId?: string;
  maxQueue?: number;
  batchSize?: number;
  flushMs?: number;
}

export interface RunOptions {
  runId?: string;
  traceId?: string;
  workflowId?: string;
  triggerEventId?: string;
}

export interface ObserverStats {
  accepted: number;
  dropped: number;
  sendFailures: number;
  batchesSent: number;
  queued: number;
}

export class AgentObserver {
  constructor(agentName: string, options?: ObserverOptions);
  startRun(options?: RunOptions): AgentRun;
  withRun<T>(fn: (run: AgentRun) => Promise<T> | T, options?: RunOptions): Promise<T>;
  flush(): Promise<void>;
  shutdown(): Promise<void>;
  stats(): ObserverStats;
}

export class AgentRun {
  readonly runId: string;
  readonly traceId: string;
  readonly workflowId: string;
  readonly closed: boolean;
  start(): this;
  finish(status?: 'success' | 'error' | 'cancelled'): boolean;
  tool<T>(name: string, options: {category?: ToolCategory}, fn: () => Promise<T> | T): Promise<T>;
  tool<T>(name: string, fn: () => Promise<T> | T): Promise<T>;
  model<T>(options: {model?: string; usage?: TokenUsage}, fn: () => Promise<T> | T): Promise<T>;
  model<T>(fn: () => Promise<T> | T): Promise<T>;
  handoff(): boolean;
  approvalRequested(): boolean;
  approvalReceived(approved?: boolean): boolean;
  recordError(): boolean;
}
