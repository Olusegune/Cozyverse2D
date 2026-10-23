export interface DocSection { id: string; title: string; body: string[] }

export const QUICK_START: { title: string; text: string }[] = [
  { title: "Start with an image", text: "On Home choose Create Diorama or Create Regular Image to generate one from a prompt in any art style, or Import Image to bring your own. No account is needed: the built-in generator and local tools work offline." },
  { title: "Approve one image", text: "Compare the variations, edit them if you like (crop, add exact sign lettering, flip, or AI edit), then press Approve for Breakdown. Nothing is broken down or sent anywhere before you approve." },
  { title: "Break it down", text: "In Decompose, drag boxes around objects, or use Detect objects to get proposals. Proposals start unconfirmed so you can review them. Group parts under parents, merge duplicates and set what you want from each item." },
  { title: "Build the 3D model", text: "In 3D Studio choose an item and press Build Proxy, or import your own .glb. The proxy is an editable stand-in made from simple shapes. Every view is rendered from this model, never invented by an image generator." },
  { title: "Render 5 Views", text: "Press Render 5 Views. Blender renders front, back, left and right as true orthographic cameras and perspective as a separate 3/4 camera: five separate PNG files, never a contact sheet. Re-render any single view on its own." },
  { title: "Review, then export", text: "Approve or reject each view, run the Quality checks, then open Exports. The ZIP says PARTIAL, with reasons, until everything is approved." },
];

export const DOCS: DocSection[] = [
  { id: "overview", title: "Overview", body: [
    "CozyVerse Breakdown creates or imports an image of a diorama or any scene, breaks it into assets, and produces a reference package for 3D tools such as Tripo or Meshy.",
    "The 3D model, not an image generator, decides what each angle looks like. Every view is a Blender render of the same model revision.",
    "Everything core runs offline on your computer. AI providers are optional and always ask for your approval before anything leaves the machine or costs money.",
  ] },
  { id: "modes", title: "Diorama and regular images", body: [
    "A project is either a Diorama (a miniature on a display base) or a Regular image (a painting, photo, character, poster, scene). You can switch later without losing images, edits or styles.",
    "Regular images never get a base unless you opt in. Diorama projects can have a master base, which is its own asset.",
    "Each item can be exported as 2D layers, as an unverified 2D reference, as a 3D proxy, or as a mesh with five views.",
  ] },
  { id: "styles", title: "Styles", body: [
    "The Style Library describes looks by their traits (medium, line, palette, texture, lighting): 2D illustration and paint, handmade 3D, CG, and photographic looks. They work the same in both modes.",
    "Make your own recipes, blend up to four styles, preview the exact prompt, and import or export recipe files. Prompts are never rewritten behind your back: Review before generating shows the exact text.",
  ] },
  { id: "generate", title: "Creating and editing images", body: [
    "Pick a provider and model. Options such as aspect ratio, resolution, variations, seed and reference images appear only if the model supports them, and unsupported choices are disabled with the reason.",
    "Every image is saved as a new, immutable version with its prompt, model, style and seed. Edits never overwrite the original. Use Compare to swipe between versions.",
    "Local edits (crop, text overlay, flip) are free. AI edits can use a drawn region when the model supports masks.",
  ] },
  { id: "decompose", title: "Breaking an image down", body: [
    "Items are boxes on the approved image with a stable code such as BLD-001. Tiny props count. Parts can belong to a parent (a sign on a building), duplicates can be merged, and extra copies are kept as instances.",
    "Detect objects only proposes items. Review them, confirm the good ones and delete the rest. The audit lists tiny items and empty image zones where small props may hide.",
    "Every item remembers which exact image version it came from.",
  ] },
  { id: "views", title: "The five views", body: [
    "FRONT, BACK, LEFT and RIGHT are orthographic: no perspective, no tilt, no adjacent walls visible. PERSPECTIVE is a separate 3/4 camera with its own corner, elevation and focal length.",
    "Each view is its own render and its own PNG: views/front.png, back.png, left.png, right.png, perspective.png. Hover a card to see its camera, scale, base policy, engine and model revision.",
    "Back and side faces are inferred from a single image. They are marked as inferred; review them before approving.",
  ] },
  { id: "models3d", title: "3D models", body: [
    "Build Proxy makes an editable model from simple shapes: width, depth, height, colour, roof, awning and so on. Import .glb uses your own model. Generate 3D with AI downloads a model from a provider you connected.",
    "Imported and AI models are always reviewed first: choose the rotation and height, check that the front faces the front view, then Approve mesh.",
    "Changing the model marks existing views stale until you re-render them.",
  ] },
  { id: "base", title: "Bases and plinths", body: [
    "None: the asset only. Building foundation: footing and entry steps that belong to the building. Local plinth: a display slab under one asset. Master base: the whole diorama platform, its own asset (BASE-001) with five views and a top view.",
    "Defaults: buildings use foundation for elevations and plinth for perspective. Props use none. Use one policy for all five views when you need dimension-matched references for 3D generation.",
    "Edit the master base in 3D Studio: size, thickness, bevel, border, road lanes and material.",
  ] },
  { id: "qa", title: "Quality checks", body: [
    "Automatic checks cover size, pure white background, cropping, margins and the camera Blender really used.",
    "Quality analysis looks for a colour cast, noise and (as a hint only) waxy skin. It is heuristic: warm lamps and neon are protected, texture such as rust is not treated as noise, and a single warm surface is flagged for you to judge rather than as a defect.",
    "A fix is previewed first with before, after, a difference map and the mask. Accepting is your decision, the original is always kept, and Restore original undoes it. Re-rendering is usually better than repairing pixels.",
  ] },
  { id: "export", title: "Review and export", body: [
    "Renders arrive as Draft or Needs review. Only you can mark a view Approved.",
    "Export reports COMPLETE only when every asset is complete: all five views approved for 3D items, and a crop for 2D items. Otherwise it is PARTIAL and lists exactly what is missing.",
    "The ZIP holds the approved source with its lineage, the inventory with history, per-asset manifests, crops and masks, 2D layers, models, camera data and QA reports. You can add a supplementary contact sheet; it never replaces the five files.",
  ] },
  { id: "costs", title: "Providers, keys and costs", body: [
    "In Models and Costs, connect your own accounts for Google, OpenAI, Kie and Meshy. Keys live in the Windows credential store, never in project files or logs.",
    "Nothing is assumed about prices. Enter your own per-image or per-task price to get estimates and spending caps; otherwise the cost shows as unknown and needs your explicit approval.",
    "Every online request shows exactly what leaves your computer (prompt text, re-encoded images without metadata) and needs a ticked approval. Local-only mode blocks all online providers. Cancelling a started online task does not refund it.",
    "A model is marked verified only after a real request succeeds. Tripo is listed but disabled until its request format can be verified.",
  ] },
  { id: "privacy", title: "Privacy", body: [
    "No telemetry. Your source images stay on this computer unless you approve sending one to a provider you connected. Originals are never modified.",
    "Local Blender renders are free and unlimited.",
  ] },
  { id: "keys", title: "Shortcuts", body: [
    "F1: open Documentation. Esc: close a dialog or the splash screen. Tab and Shift+Tab move between controls.",
  ] },
  { id: "trouble", title: "Troubleshooting", body: [
    "Blender not found: open Settings, paste the path to blender.exe and press Test and save.",
    "Render fails: the job drawer shows the reason. Try the Workbench engine to confirm the model is valid, then Cycles for final quality.",
    "A provider says it is not entitled or the model is disabled: check your key and plan with the provider, then press Check connection.",
    "Local worker not reachable at startup: press Retry on the splash screen, or restart the app.",
  ] },
];
