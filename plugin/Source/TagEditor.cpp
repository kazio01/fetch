#include "TagEditor.h"

namespace
{
struct Label : juce::Component
{
    explicit Label (const juce::String& t, bool sub = false) : text (t), sublevel (sub) { setInterceptsMouseClicks (false, false); }
    void paint (juce::Graphics& g) override
    {
        if (sublevel)
        {
            g.setColour (theme::accentBg.withAlpha (0.5f));
            g.fillRect (0, 0, 2, getHeight());
        }
        g.setColour (theme::fg.withMultipliedAlpha (0.55f));   // .sh-filter-label
        g.setFont (theme::font (11.7f));
        g.drawText (text, sublevel ? 7 : 0, 0, getWidth(), 18, juce::Justification::centredLeft);
    }
    juce::String text;
    bool sublevel;
};
} // namespace

struct TagEditor::Body : juce::Component
{
    explicit Body (TagEditor& o) : owner (o) {}

    using Spec = std::tuple<juce::String, bool, std::function<void()>, juce::String>;

    int build (int w)
    {
        items.clear();
        int y = 0;
        auto label = [&] (const juce::String& t) {
            auto* l = new Label (t);
            items.add (l);
            addAndMakeVisible (l);
            l->setBounds (0, y, w, 20);
            y += 22;
        };
        auto flow = [&] (const juce::Array<Spec>& chips, int indent, int cols = 3) {
            const int gap = 6, ch = 26;
            const int cw = (w - indent - gap * (cols - 1)) / cols;
            for (int i = 0; i < chips.size(); ++i)
            {
                auto& [text, on, click, tip] = chips.getReference (i);
                auto* c = new Chip (text, on, click, tip);
                items.add (c);
                addAndMakeVisible (c);
                c->setBounds (indent + (i % cols) * (cw + gap), y + (i / cols) * (ch + gap), cw, ch);
            }
            y += ((chips.size() + cols - 1) / cols) * (ch + gap) + 4;
        };
        auto& o = owner;

        label ("Type");
        juce::Array<Spec> types;
        if (auto* ts = o.catalog["types"].getArray())
            for (auto& t : *ts)
            {
                const auto name = t["name"].toString();
                types.add ({ t["label"].toString(), o.type == name, [&o, name] {
                                 o.type = name;
                                 o.touched.addIfNotAlreadyThere ("type");
                                 o.render();
                             }, t["label"].toString() });
            }
        flow (types, 0, juce::jmax (1, types.size()));

        label ("Instrument");
        juce::Array<Spec> mains;
        const auto* tree = o.catalog["inst_tree"].getArray();
        if (tree != nullptr)
            for (auto& m : *tree)
            {
                const auto id = m["id"].toString();
                mains.add ({ m["label"].toString(), o.path.size() > 0 && o.path[0] == id, [&o, id] { pick (o, 0, id); },
                             m["path"].toString() });
            }
        flow (mains, 0);
        // deeper levels along the chosen path
        for (int depth = 0; depth < o.path.size(); ++depth)
        {
            const auto* node = o.nodeById (o.path[depth]);
            if (node == nullptr)
                break;
            auto* kids = (*node)["children"].getArray();
            if (kids == nullptr || kids->isEmpty())
                break;
            const int top = y;
            auto* l = new Label ((*node)["path"].toString() + juce::String::fromUTF8 (" \xe2\x80\xba"), true);
            items.add (l);
            addAndMakeVisible (l);
            y += 20;
            juce::Array<Spec> k;
            for (auto& c : *kids)
            {
                const auto id = c["id"].toString();
                const bool on = o.path.size() > depth + 1 && o.path[depth + 1] == id;
                k.add ({ c["label"].toString(), on, [&o, depth, id] { pick (o, depth + 1, id); }, c["path"].toString() });
            }
            flow (k, 9);
            l->setBounds (2, top, w - 2, y - top - 4);
        }

        if (auto* groups = o.catalog["tag_groups"].getArray())
            for (auto& g : *groups)
            {
                label (g["group"].toString());
                juce::Array<Spec> chips;
                if (auto* ts = g["tags"].getArray())
                    for (auto& t : *ts)
                    {
                        const auto id = t["id"].toString(), text = t["label"].toString();
                        bool est = o.tags.contains (id) && ! o.touchedTags.contains (id);
                        for (auto& s : o.samples)
                            est = est && s.itagsEst.contains (id);
                        chips.add ({ text + (est ? " ~" : ""), o.tags.contains (id), [&o, id] {
                                         if (o.tags.contains (id))
                                             o.tags.removeString (id);
                                         else
                                             o.tags.add (id);
                                         o.touchedTags.addIfNotAlreadyThere (id);
                                         o.touched.addIfNotAlreadyThere ("tags");
                                         o.render();
                                     }, est ? "Estimated from the sound" : text });
                    }
                flow (chips, 0);
            }
        return y;
    }

    static void pick (TagEditor& o, int depth, const juce::String& node)
    {
        if (o.path.size() > depth && o.path[depth] == node && depth > 0)
            o.path.removeRange (depth, o.path.size() - depth);
        else
        {
            o.path.removeRange (depth, o.path.size() - depth);
            o.path.add (node);
        }
        o.inst = o.path.isEmpty() ? juce::String() : o.path[o.path.size() - 1];
        o.touched.addIfNotAlreadyThere ("inst");
        o.render();
    }

    TagEditor& owner;
    juce::OwnedArray<juce::Component> items;
};

TagEditor::TagEditor (FetchEditor& e, const juce::Array<SampleInfo>& s, const juce::var& cat)
    : ed (e), samples (s), catalog (cat)
{
    setWantsKeyboardFocus (true);
    auto same = [this] (std::function<juce::String (const SampleInfo&)> f) {
        const auto v = f (samples.getReference (0));
        for (auto& x : samples)
            if (f (x) != v)
                return juce::String();
        return v;
    };
    const auto t = same ([] (const SampleInfo& x) { return x.type; });
    type = t == "Loop" ? "loop" : t == "One-shot" ? "oneshot" : juce::String();
    inst = same ([] (const SampleInfo& x) { return x.instId; });
    path = pathOf (inst);
    tags = samples.getReference (0).itags;
    for (auto& x : samples)
        for (int i = tags.size(); --i >= 0;)
            if (! x.itags.contains (tags[i]))
                tags.remove (i);

    body = std::make_unique<Body> (*this);
    viewport.setViewedComponent (body.get(), false);
    viewport.setScrollBarsShown (true, false);
    viewport.setWantsKeyboardFocus (false);
    addAndMakeVisible (viewport);

    automatic.setTooltip ("Forget your changes for the selected samples");
    automatic.onClick = [this] { save (true); };
    cancel.onClick = [this] { ed.closeTagEditor(); };
    saveButton.getProperties().set ("suggested", true);
    saveButton.onClick = [this] { save (false); };
    for (auto* b : { &automatic, &cancel, &saveButton })
    {
        b->setWantsKeyboardFocus (false);
        addAndMakeVisible (b);
    }
}

TagEditor::~TagEditor() = default;

juce::StringArray TagEditor::pathOf (const juce::String& node) const
{
    juce::StringArray out;
    auto parts = juce::StringArray::fromTokens (node, "/", "");
    parts.removeEmptyStrings();
    for (int i = 0; i < parts.size(); ++i)
        out.add (parts.joinIntoString ("/", 0, i + 1));
    return out;
}

const juce::var* TagEditor::nodeById (const juce::String& id) const
{
    std::function<const juce::var* (const juce::var&)> find = [&] (const juce::var& list) -> const juce::var* {
        if (auto* a = list.getArray())
            for (auto& n : *a)
            {
                if (n["id"].toString() == id)
                    return &n;
                if (auto* r = find (n["children"]))
                    return r;
            }
        return nullptr;
    };
    return find (catalog["inst_tree"]);
}

void TagEditor::render()
{
    juce::MessageManager::callAsync ([sp = juce::Component::SafePointer<TagEditor> (this)] {   // not inside a chip's click
        if (sp != nullptr)
            sp->resized();
    });
}

void TagEditor::paint (juce::Graphics& g)
{
    g.fillAll (juce::Colours::black.withAlpha (theme::dark ? 0.5f : 0.3f));   // dims the window (Adw.Dialog)
    auto c = card.toFloat();
    g.setColour (juce::Colours::black.withAlpha (0.25f));
    g.fillRoundedRectangle (c.translated (0, 3).expanded (2), 14.0f);
    g.setColour (theme::popup);
    g.fillRoundedRectangle (c, 12.0f);
    g.setColour (theme::fg);
    g.setFont (theme::font (theme::body, true));
    g.drawText ("Edit tags", card.withHeight (46), juce::Justification::centred);
    g.setFont (theme::font (15.5f, true));   // .heading
    const auto name = samples.size() == 1 ? samples.getReference (0).name : juce::String (samples.size()) + " samples selected";
    g.drawText (name, card.reduced (16, 0).withTrimmedTop (46).withHeight (28), juce::Justification::centredLeft, true);
    if (error.isNotEmpty())
    {
        g.setColour (theme::liked);
        g.setFont (theme::font (12.5f));
        g.drawText (error, card.reduced (16, 0).withTrimmedBottom (8).removeFromBottom (64).withHeight (16),
                    juce::Justification::centredLeft, true);
    }
}

void TagEditor::resized()
{
    card = getLocalBounds().withSizeKeepingCentre (juce::jmin (440, getWidth() - 40), juce::jmin (620, getHeight() - 40));
    auto r = card.reduced (16, 0);
    r.removeFromTop (46 + 28 + 8);
    auto buttons = r.removeFromBottom (16 + 34).withTrimmedBottom (16);
    automatic.setBounds (buttons.removeFromLeft (110));
    saveButton.setBounds (buttons.removeFromRight (80));
    buttons.removeFromRight (8);
    cancel.setBounds (buttons.removeFromRight (80));
    r.removeFromBottom (24);
    viewport.setBounds (r);
    const auto pos = viewport.getViewPosition();
    const int w = r.getWidth() - 12;
    body->setSize (w, body->build (w));
    viewport.setViewPosition (pos);
    repaint();
}

bool TagEditor::keyPressed (const juce::KeyPress& k)
{
    if (k == juce::KeyPress::escapeKey)
    {
        ed.closeTagEditor();
        return true;
    }
    if (k == juce::KeyPress::returnKey)
    {
        save (false);
        return true;
    }
    return true;   // the dialog is modal: keys don't reach the list behind it
}

void TagEditor::mouseDown (const juce::MouseEvent& e)
{
    if (! card.contains (e.getPosition()))
        ed.closeTagEditor();   // clicking outside closes, as an Adw.Dialog
}

juce::var TagEditor::changes() const
{
    auto* o = new juce::DynamicObject();
    juce::Array<juce::var> t;
    for (auto& x : tags)
        t.add (x);
    if (samples.size() == 1)
    {
        if (type.isNotEmpty())
            o->setProperty ("type", type);
        if (inst.isNotEmpty())
            o->setProperty ("inst", inst);
        o->setProperty ("tags", t);
    }
    else
    {
        if (touched.contains ("type"))
            o->setProperty ("type", type);
        if (touched.contains ("inst"))
            o->setProperty ("inst", inst);
        if (touched.contains ("tags"))
            o->setProperty ("tags", t);
    }
    return juce::var (o);
}

void TagEditor::save (bool automaticTags)
{
    if (saving)
        return;
    const auto tagsToSave = automaticTags ? juce::var() : changes();
    if (! automaticTags && tagsToSave.getDynamicObject()->getProperties().isEmpty())
    {
        ed.closeTagEditor();
        return;
    }
    saving = true;
    juce::StringArray paths;
    for (auto& s : samples)
        paths.add (s.path);
    auto* client = &ed.proc.client;
    ed.pool->addJob ([this, client, paths, tagsToSave, sp = juce::Component::SafePointer<TagEditor> (this)] {
        juce::String err;
        auto out = client->setTags (paths, tagsToSave, err);
        juce::MessageManager::callAsync ([this, sp, out, err] {
            if (sp == nullptr)
                return;
            saving = false;
            if (err.isNotEmpty())
            {
                error = err;
                repaint();
                return;
            }
            ed.tagsSaved (out);
        });
    });
}
