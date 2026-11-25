import QuartzCore
import SwiftUI
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

/// The live part of the brand mark: its halo breathing slowly, as on the tickr.bot site (from
/// `Opacity.muted` to full and from its size to `BrandMark.haloScale` times it, and back, every
/// `BrandMark.haloSeconds`). Core Animation runs it in the render server. As a SwiftUI
/// `repeatForever` animation it moved every display frame inside the Overview's header, and each
/// frame laid out the whole Overview again, texts included: about a full CPU core at idle
/// (Instruments, 2026-11-15).
struct BrandMarkLive {
  /// Side of the halo, in points.
  let size: CGFloat
  let color: Color
  /// Whether the scene is in front. Core Animation drops a layer's loops when the app goes to the
  /// background; a change here updates the view, which starts them again.
  let active: Bool
}

/// Holds the halo layer and its loop.
final class BrandMarkLiveView: PlatformView {
  /// Key of the loop on the halo layer.
  static let haloKey = "halo"
  /// Key paths the loop drives.
  static let scalePath = "transform.scale"
  static let opacityPath = "opacity"
  /// A radial gradient's end point at the layer's corner: the glow fades out at its edge.
  static let centre = CGPoint(x: 0.5, y: 0.5)
  static let edge = CGPoint(x: 1, y: 1)

  private let root = CALayer()
  private let halo = CAGradientLayer()
  private var laidOutSize: CGFloat?

  override init(frame: CGRect) {
    super.init(frame: frame)
    #if canImport(UIKit)
    layer.addSublayer(root)
    #else
    // A layer-hosting view: set our own layer before asking for one, as AppKit documents for
    // views that add sublayers.
    let host = CALayer()
    layer = host
    wantsLayer = true
    host.addSublayer(root)
    #endif
    halo.type = .radial
    halo.startPoint = Self.centre
    halo.endPoint = Self.edge
    root.addSublayer(halo)
  }

  @available(*, unavailable)
  required init?(coder: NSCoder) { nil }

  // An ornament: clicks go through to what is under it (on the Mac, dragging the window).
  #if canImport(UIKit)
  override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? { nil }
  #else
  override func hitTest(_ point: NSPoint) -> NSView? { nil }
  #endif

  // Core Animation drops a layer's animation when its window goes away (or the app goes to the
  // background on iOS); start them again when the view is back on screen.
  #if canImport(UIKit)
  override func didMoveToWindow() {
    super.didMoveToWindow()
    if window != nil, laidOutSize != nil { startLoop() }
  }
  #else
  override func viewDidMoveToWindow() {
    super.viewDidMoveToWindow()
    if window != nil, laidOutSize != nil { startLoop() }
  }
  #endif

  /// Shows `mark` with its colour resolved for `environment`; lays out and starts the loop on the
  /// first show and when the size changes.
  func show(_ mark: BrandMarkLive, environment: EnvironmentValues) {
    CATransaction.begin()
    CATransaction.setDisableActions(true)
    var inner = mark.color.resolve(in: environment)
    inner.opacity = Float(Tokens.Opacity.faint)
    var outer = inner
    outer.opacity = 0
    halo.colors = [inner.cgColor, outer.cgColor]
    if laidOutSize != mark.size { layOut(size: mark.size) }
    CATransaction.commit()
    if halo.animation(forKey: Self.haloKey) == nil { startLoop() }
  }

  private func layOut(size: CGFloat) {
    laidOutSize = size
    let bounds = CGRect(x: 0, y: 0, width: size, height: size)
    root.frame = bounds
    halo.frame = bounds
    startLoop()
  }

  /// The halo brightens and grows, then settles, easing in and out, once every breath.
  private func startLoop() {
    let grow = CABasicAnimation(keyPath: Self.scalePath)
    grow.fromValue = 1
    grow.toValue = BrandMark.haloScale
    let brighten = CABasicAnimation(keyPath: Self.opacityPath)
    brighten.fromValue = Tokens.Opacity.muted
    brighten.toValue = 1
    let breath = CAAnimationGroup()
    breath.animations = [grow, brighten]
    // Half a breath out, the same back.
    breath.duration = BrandMark.haloSeconds / 2
    breath.autoreverses = true
    breath.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
    // decorative: the brand mark's halo is an ornament (owner, 2026-11-15).
    breath.repeatCount = .infinity
    breath.isRemovedOnCompletion = false
    halo.opacity = Float(Tokens.Opacity.muted)
    halo.add(breath, forKey: Self.haloKey)
  }
}

#if canImport(UIKit)
extension BrandMarkLive: UIViewRepresentable {
  func makeUIView(context: Context) -> BrandMarkLiveView { BrandMarkLiveView(frame: .zero) }
  func updateUIView(_ view: BrandMarkLiveView, context: Context) { view.show(self, environment: context.environment) }
}
#elseif canImport(AppKit)
extension BrandMarkLive: NSViewRepresentable {
  func makeNSView(context: Context) -> BrandMarkLiveView { BrandMarkLiveView(frame: .zero) }
  func updateNSView(_ view: BrandMarkLiveView, context: Context) { view.show(self, environment: context.environment) }
}
#endif
