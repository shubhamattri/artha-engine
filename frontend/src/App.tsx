import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import RequireAuth from './components/RequireAuth'
import Login from './pages/Login'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Transactions from './pages/Transactions'
import Today from './pages/Today'
import Commitments from './pages/Commitments'
import Year from './pages/Year'
import Plan from './pages/Plan'
import Balance from './pages/Balance'
import DebtDetail from './pages/DebtDetail'
import Profile from './pages/Profile'
import Settings from './pages/Settings'
import Admin from './pages/Admin'
import HistoryPage from './pages/HistoryPage'
import AskArtha from './pages/AskArtha'
import Insights from './pages/Insights'
import Windfall from './pages/Windfall'
import {
  FixedLedger,
  IncomeLedger,
  SavingsLinesLedger,
  SinkingLedger,
  WalletLedger,
} from './pages/PlanLedgers'
import { FundDetail, FundsPortfolio } from './pages/Funds'
import {
  AssetsLedger,
  DisposalsLedger,
  GoldLedger,
  InsuranceLedger,
  InvestmentsLedger,
} from './pages/WealthLedgers'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* public auth routes */}
        <Route path="/login" element={<Login />} />
        <Route path="/forgot" element={<ForgotPassword />} />
        <Route path="/reset" element={<ResetPassword />} />

        {/* everything else requires a session */}
        <Route element={<RequireAuth />}>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/today" replace />} />

            {/* FOUR tabs. Each is a composition of screens that used to be destinations of their
                own — the merge is in the page, not here, so every part stays independently routable
                and independently testable. */}
            <Route path="today" element={<Today />} />
            <Route path="spending" element={<Transactions />} />
            <Route path="plan" element={<Plan />} />
            <Route path="balance" element={<Balance />} />
            <Route path="balance/debt" element={<DebtDetail />} />

            {/* The Plan owns zero forms — every manager it used to stack inline is a destination
                under it, the same shape the Wealth ledgers already used. */}
            <Route path="plan/income" element={<IncomeLedger />} />
            <Route path="plan/fixed" element={<FixedLedger />} />
            <Route path="plan/sinking" element={<SinkingLedger />} />
            <Route path="plan/savings" element={<SavingsLinesLedger />} />
            {/* A destination, reached from Plan's ledger rows. There was no /wallet route at all —
                the card rendered at the top of the Plan tab and the path rendered a blank page. */}
            <Route path="wallet" element={<WalletLedger />} />

            {/* Tools: invoked, not inhabited. No nav slot — Windfall opens when a lump sum lands,
                Ask Artha from the header affordance every tab already carries. */}
            <Route path="windfall" element={<Windfall />} />
            <Route path="ask" element={<AskArtha />} />
            {/* A destination, reached from Today. Not a tab and not a section of one: Today ranks
                ONE move, and the rest of what the checks found belongs where reading it is a choice. */}
            <Route path="insights" element={<Insights />} />

            {/* Every path the old seven-tab nav could produce still resolves, because a bookmark,
                an iPhone home-screen shortcut and an in-app link all outlive an IA change. They
                REDIRECT rather than alias: two live URLs for one screen is how the app ends up with
                two of everything again. */}
            <Route path="dashboard" element={<Navigate to="/today" replace />} />
            {/* A destination, reached from Today's "Coming up". It was redirecting to /today, which
                made that link point back at the page it was already on. */}
            <Route path="commitments" element={<Commitments />} />
            <Route path="transactions" element={<Navigate to="/spending" replace />} />
            <Route path="budget" element={<Navigate to="/plan" replace />} />
            <Route path="analytics" element={<Navigate to="/plan" replace />} />
            {/* A destination, reached from the Year band inside Plan > History. */}
            <Route path="year" element={<Year />} />
            {/* Every change anyone has made. Reached from the avatar menu and from a transaction's
                own history, not from the nav — it is where you go when a number surprises you. */}
            <Route path="history" element={<HistoryPage />} />
            <Route path="debt" element={<Navigate to="/balance" replace />} />
            <Route path="savings" element={<Navigate to="/balance" replace />} />
            <Route path="wealth" element={<Navigate to="/balance" replace />} />
            {/* The Wealth tab owns zero forms — every CRUD manager is a destination under it. */}
            {/* Funds are the one holding a household re-reads, so they get a read-first screen of
                their own. Editing still lives in the ledger at wealth/investments. */}
            <Route path="wealth/funds" element={<FundsPortfolio />} />
            <Route path="wealth/funds/:id" element={<FundDetail />} />
            <Route path="wealth/assets" element={<AssetsLedger />} />
            <Route path="wealth/investments" element={<InvestmentsLedger />} />
            <Route path="wealth/gold" element={<GoldLedger />} />
            <Route path="wealth/insurance" element={<InsuranceLedger />} />
            <Route path="wealth/disposals" element={<DisposalsLedger />} />
            <Route path="profile" element={<Profile />} />
            <Route path="settings" element={<Settings />} />
            {/* Super admin only. The screen says so and the SERVER enforces it — every /admin route
                carries require_super_admin, so reaching this URL as a member shows an empty page
                rather than someone else's household. */}
            <Route path="admin" element={<Admin />} />
            {/* Anything else lands on Today rather than on a blank shell. Every redirect above exists
                because "a bookmark, an iPhone home-screen shortcut and an in-app link all outlive an
                IA change" — but with no fallback, one path nobody thought to redirect still rendered
                an empty page inside the chrome, which reads as the app being broken rather than as a
                dead link. /wallet was exactly that until this commit. */}
            <Route path="*" element={<Navigate to="/today" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
