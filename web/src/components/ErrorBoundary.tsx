import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Last line of defence: a render error shows a plain reload card instead of a blank page. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Beacon console crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="shell">
        <main className="main">
          <div className="panel" role="alert">
            <div className="eyebrow">Console error</div>
            <p className="faint small" style={{ margin: "8px 0 12px" }}>
              {this.state.error.message}
            </p>
            <button className="btn primary" type="button" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </main>
      </div>
    );
  }
}
