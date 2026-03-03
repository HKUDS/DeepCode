import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from './components/common/Toaster'
import Layout from './components/layout/Layout'
import HomePage from './pages/HomePage'
import PaperToCodePage from './pages/PaperToCodePage'
import ChatPlanningPage from './pages/ChatPlanningPage'
import WorkflowEditorPage from './pages/WorkflowEditorPage'
import SettingsPage from './pages/SettingsPage'

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/paper-to-code" element={<PaperToCodePage />} />
          <Route path="/chat" element={<ChatPlanningPage />} />
          <Route path="/workflow" element={<WorkflowEditorPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Layout>
      <Toaster />
    </BrowserRouter>
  )
}

export default App
