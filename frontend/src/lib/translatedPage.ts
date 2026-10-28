/**
 * When the browser translates the page (Chrome, Edge, extensions), it puts its own elements in place of text nodes.
 * React keeps the old nodes and fails on its next insert or removal with "insertBefore" or "removeChild"; the whole
 * page is gone then. Here both give way: a node already taken away is not removed a second time, and what should go
 * before a node taken away goes before what holds it now, otherwise at the end.
 */
export function bearTranslatedPages(): void {
  if (typeof Node !== 'function') return
  const proto = Node.prototype
  if ((proto as { nextrmnlBears?: boolean }).nextrmnlBears) return
  ;(proto as { nextrmnlBears?: boolean }).nextrmnlBears = true

  const removeChild = proto.removeChild
  proto.removeChild = function <T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) return child
    return removeChild.call(this, child) as T
  }

  const insertBefore = proto.insertBefore
  proto.insertBefore = function <T extends Node>(this: Node, node: T, before: Node | null): T {
    if (before !== null && before.parentNode !== this) {
      return insertBefore.call(this, node, childHolding(this, before)) as T
    }
    return insertBefore.call(this, node, before) as T
  }
}

/** The child of `parent` that holds `node` today; null when it is no longer in there. */
function childHolding(parent: Node, node: Node): Node | null {
  let current: Node | null = node
  while (current !== null && current.parentNode !== parent) current = current.parentNode
  return current
}
