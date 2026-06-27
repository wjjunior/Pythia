import { createBrowserRouter } from "react-router-dom"

import { AppShell } from "@/components/app-shell"
import { ArbView } from "@/views/arb-view"
import { CalibrationView } from "@/views/calibration-view"
import { CostView } from "@/views/cost-view"
import { GrowthView } from "@/views/growth-view"
import { MarketDetailView } from "@/views/market-detail-view"
import { PaperView } from "@/views/paper-view"
import { RadarView } from "@/views/radar-view"
import { SmartMoneyView } from "@/views/smartmoney-view"

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <RadarView /> },
      { path: "/smart-money", element: <SmartMoneyView /> },
      { path: "/arb", element: <ArbView /> },
      { path: "/markets/:id", element: <MarketDetailView /> },
      { path: "/paper", element: <PaperView /> },
      { path: "/calibration", element: <CalibrationView /> },
      { path: "/cost", element: <CostView /> },
      { path: "/growth", element: <GrowthView /> },
    ],
  },
])
