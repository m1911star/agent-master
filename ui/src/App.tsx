/**
 * Top-level shell. Routing comes online in a later commit; for now App is
 * the single-page home view so we can validate theme + data flow.
 */

import { Home } from "./pages/Home"
import { Header } from "./components/layout/Header"
import { StatusBar } from "./components/layout/StatusBar"

export function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1 px-6 py-5">
        <Home />
      </main>
      <StatusBar />
    </div>
  )
}
