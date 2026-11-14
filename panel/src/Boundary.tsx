// A crash while drawing one screen shows a short message with «Retry» in its place, never a blank page: the shell
// (sidebar, bar, project picker) stays. `key` it by screen and project so changing either starts clean.
import { Component, type ReactNode } from "react";
import { t } from "./lib";

interface State {
  failed: boolean;
}

export class Boundary extends Component<{ children: ReactNode }, State> {
  override state: State = { failed: false };
  static getDerivedStateFromError(): State {
    return { failed: true };
  }
  override componentDidCatch(e: unknown): void {
    console.error("screen crashed", e);
  }
  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="card quiet" role="alert">
        {t("screen_failed")}{" "}
        <button type="button" className="link" onClick={() => this.setState({ failed: false })}>
          {t("retry")}
        </button>
      </div>
    );
  }
}
