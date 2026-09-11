import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import App from './App'
import EventsPage from './pages/EventsPage'
import MyThesisPage from './pages/MyThesisPage'
import OpportunityPage from './pages/OpportunityPage'
import ProfilePage from './pages/ProfilePage'
import RadarPage from './pages/RadarPage'
import './styles/theme.css'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <RadarPage /> },
      { path: 'opportunities/:id', element: <OpportunityPage /> },
      { path: 'thesis', element: <MyThesisPage /> },
      { path: 'events', element: <EventsPage /> },
      { path: 'profile', element: <ProfilePage /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
