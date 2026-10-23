export interface DocSection { id: string; title: string; body: string[] }

export const QUICK_START: { title: string; text: string }[] = [
  { title: "Open the sample project", text: "From the welcome screen choose Open sample project. It contains the BAR building (BLD-003) and the master diorama base (BASE-001)." },
  { title: "Build the proxy", text: "Select an asset and press Build Proxy. This creates the editable 3D stand-in that every view is rendered from. Nothing is rendered without a model." },
  { title: "Render 5 Views", text: "Press Render 5 Views. Blender renders front, back, left and right as true orthographic cameras and perspective as a separate 3/4 camera. Five separate PNG files, never a contact sheet." },
  { title: "Review each view", text: "Look at every card. Approve what is correct, reject what is not. Back and side walls are inferred from a single source image, so check them." },
  { title: "Export the ZIP", text: "Export ZIP packages the images, the 3D proxy, camera metadata and QA reports. The export says PARTIAL until every asset is approved." },
];

export const DOCS: DocSection[] = [
  { id: "overview", title: "Overview", body: [
    "CozyVerse Breakdown turns an image of a diorama or any scene into a list of assets and a reference package for 3D tools such as Tripo or Meshy.",
    "The 3D model, not an image generator, decides what each angle looks like. Every view is a Blender render of the same model revision.",
    "Everything core runs offline on your computer. AI providers are optional and always ask for your approval before anything leaves the machine or costs money.",
  ] },
  { id: "views", title: "The five views", body: [
    "FRONT, BACK, LEFT and RIGHT are orthographic: no perspective, no tilt, no adjacent walls visible.",
    "PERSPECTIVE is a separate 3/4 camera. You can choose the corner, elevation and focal length.",
    "Each view is its own render call and its own PNG: views/front.png, back.png, left.png, right.png, perspective.png.",
    "Hover a card to see its camera direction, scale, base policy, engine and model revision.",
  ] },
  { id: "base", title: "Bases and plinths", body: [
    "None: the asset only. Building foundation: footing and entry steps that belong to the building. Local plinth: a display slab under one asset. Master base: the whole diorama platform, which is its own asset (BASE-001).",
    "Defaults: buildings use foundation for elevations and plinth for perspective. Props use none. Use one policy for all five views when you need dimension-matched references for 3D generation.",
    "Regular images (not dioramas) never get a base unless you turn one on.",
  ] },
  { id: "review", title: "Review and export", body: [
    "Renders arrive as Draft or Needs review. Only you can mark a view Approved.",
    "Changing the model makes existing views Stale. Re-render all five before approving or exporting.",
    "Export reports COMPLETE only when every asset has all five views approved. Otherwise it is PARTIAL and says which parts are missing.",
  ] },
  { id: "qa", title: "Quality checks", body: [
    "Automatic checks cover image size, pure white background, cropping, margins and the camera that Blender actually used.",
    "Artifact analysis flags yellow cast, noise and plastic-looking skin. It never promises artifact-free output, never removes intentional warm lamps or neon, and every fix is previewed first.",
  ] },
  { id: "privacy", title: "Privacy and cost", body: [
    "No telemetry. Your source images stay on this computer unless you approve sending them to a provider you connected.",
    "Provider keys are stored in the Windows credential store, never in project files or logs.",
    "Paid actions show an estimate and need your confirmation. Local Blender renders are free and unlimited.",
  ] },
  { id: "keys", title: "Shortcuts", body: [
    "F1: open Documentation. Esc: close a dialog or the splash screen. Tab and Shift+Tab move between controls.",
  ] },
  { id: "trouble", title: "Troubleshooting", body: [
    "Blender not found: open Render settings, paste the path to blender.exe and press Test and save.",
    "Render fails: the job drawer shows the reason. Try the Workbench engine to confirm the model is valid, then Cycles for final quality.",
    "Local worker not reachable at startup: press Retry on the splash screen, or restart the app.",
  ] },
];
